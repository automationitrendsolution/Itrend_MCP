"""
Start, stop and watch your own MCPs as local processes.

Each MCP runs as its own program (the "command" in its mcp.json), in its own
folder, on its own port. Its output goes to MCP_LOG_DIR/<slug>.log, which the
website shows on the MCP's Logs tab.

The platform remembers the process ID *and* the process start time, so a
recycled process ID belonging to some other program is never stopped by
mistake. MCP processes keep running when the website restarts.

Only administrators may start or stop MCPs (checked by the views).
"""

import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil
from django.conf import settings
from django.utils import timezone

from backend.models import MCPServer

from . import audit, catalog

logger = logging.getLogger(__name__)

# Only these variables are passed to MCP processes. The website's own secrets
# (Django secret key, database password, …) are never handed to an MCP; each
# MCP reads its own settings from the .env file in its folder.
INHERITED_ENV = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "USERNAME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ",
    "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_TOOL_DIR", "XDG_CACHE_HOME", "XDG_DATA_HOME",
}
STARTUP_GRACE_SECONDS = 2.0
STOP_TIMEOUT_SECONDS = 5.0
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_TAIL_BYTES = 256 * 1024

# Popen objects of processes started by this website process, so finished
# children are cleaned up (no zombie processes on Linux).
_children: dict[str, subprocess.Popen] = {}


class RunnerError(Exception):
    """Something the administrator should be told, in plain words."""


@dataclass(frozen=True)
class RuntimeState:
    state: str  # running | stopped | crashed | outside | unavailable
    pid: int | None = None
    started_at: datetime | None = None
    healthy: bool | None = None

    @property
    def label(self) -> str:
        return {
            "running": "Running",
            "stopped": "Stopped",
            "crashed": "Stopped unexpectedly",
            "outside": "Running (started outside the platform)",
            "unavailable": "Not available",
        }[self.state]

    @property
    def can_start(self) -> bool:
        return self.state in {"stopped", "crashed"}

    @property
    def can_stop(self) -> bool:
        return self.state == "running"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log_path(server: MCPServer) -> Path:
    return Path(settings.MCP_LOG_DIR) / f"{server.slug}.log"


def _child_env(manifest: catalog.Manifest) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() in INHERITED_ENV}
    env.update(
        {
            "PYTHONUNBUFFERED": "1",  # show output in the log immediately
            "PYTHONIOENCODING": "utf-8",
            "MCP_PORT": str(manifest.port),  # the port from mcp.json wins over the MCP's .env
            "MCP_SLUG": manifest.slug,
        }
    )
    return env


def _our_process(server: MCPServer) -> psutil.Process | None:
    """The MCP's process, if the one we started is still running."""
    if not server.process_id or server.process_create_time is None:
        return None
    try:
        process = psutil.Process(server.process_id)
        if abs(process.create_time() - server.process_create_time) > 1:
            return None  # the ID now belongs to a different program
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _is_healthy(manifest: catalog.Manifest) -> bool:
    if not manifest.health_url:
        return port_in_use(manifest.port)
    try:
        with urllib.request.urlopen(manifest.health_url, timeout=1) as response:  # noqa: S310 - local URL from mcp.json
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _rotate_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.replace(path.with_suffix(".log.1"))
    except OSError:
        logger.warning("Could not rotate %s", path)


def _note(server: MCPServer, text: str) -> None:
    """Write a platform line into the MCP's log, e.g. who started it."""
    path = log_path(server)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        # The computer's local time, like the timestamps the MCP itself prints.
        log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {text} ===\n")


def _save(server: MCPServer, **fields) -> None:
    for key, value in fields.items():
        setattr(server, key, value)
    server.save(update_fields=[*fields, "updated_at"])


def _forget_process(server: MCPServer, status: str) -> None:
    _children.pop(server.slug, None)
    _save(
        server,
        status=status,
        health_status=MCPServer.HealthStatus.UNKNOWN,
        process_id=None,
        process_create_time=None,
        started_at=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_state(server: MCPServer) -> RuntimeState:
    """
    What is really happening right now. Also corrects the stored status, for
    example when an MCP crashed or was stopped outside the platform.
    """
    manifest = catalog.manifest_for(server)
    if manifest is None:
        return RuntimeState("unavailable")

    process = _our_process(server)
    if process is not None:
        healthy = _is_healthy(manifest)
        health = MCPServer.HealthStatus.HEALTHY if healthy else MCPServer.HealthStatus.UNHEALTHY
        if server.status != MCPServer.Status.RUNNING or server.health_status != health:
            _save(server, status=MCPServer.Status.RUNNING, health_status=health)
        return RuntimeState("running", pid=process.pid, started_at=server.started_at, healthy=healthy)

    if server.process_id:  # we started it, and it is gone
        _note(server, "process ended")
        _forget_process(server, MCPServer.Status.FAILED)
        return RuntimeState("crashed")

    if port_in_use(manifest.port):
        healthy = _is_healthy(manifest)
        return RuntimeState("outside", healthy=healthy)

    if server.status == MCPServer.Status.FAILED:
        return RuntimeState("crashed")
    if server.status != MCPServer.Status.STOPPED:
        _save(server, status=MCPServer.Status.STOPPED, health_status=MCPServer.HealthStatus.UNKNOWN)
    return RuntimeState("stopped")


def start(server: MCPServer, *, by) -> RuntimeState:
    manifest = catalog.manifest_for(server)
    if manifest is None:
        raise RunnerError(catalog.manifest_problem(server) or "This MCP has no mcp.json folder, so it cannot be started here.")

    state = get_state(server)
    if state.state == "running":
        raise RunnerError("It is already running.")
    if state.state == "outside" or port_in_use(manifest.port):
        raise RunnerError(
            f"Port {manifest.port} is already in use, probably by a copy of this MCP started in a terminal. "
            "Stop that copy first (Ctrl+C in its window), then try again."
        )

    env = _child_env(manifest)
    executable = shutil.which(manifest.command[0], path=env.get("PATH"))
    if executable is None:
        raise RunnerError(f"The program “{manifest.command[0]}” from mcp.json was not found on this computer.")

    path = log_path(server)
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log(path)
    _note(server, f"started by {by.get_username() if by else 'platform'}: {' '.join(manifest.command)}")

    options: dict = {}
    if os.name == "nt":
        # Own process group: Ctrl+C in the website's terminal does not stop the MCP.
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True

    with path.open("ab") as log:
        try:
            child = subprocess.Popen(  # noqa: S603 - command comes from mcp.json in the code repository
                [executable, *manifest.command[1:]],
                cwd=manifest.folder,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                **options,
            )
        except OSError as exc:
            raise RunnerError(f"Could not start the program: {exc}") from None

    # Give it a moment: most mistakes (missing .env, bad port) make it exit at once.
    deadline = time.monotonic() + STARTUP_GRACE_SECONDS
    while time.monotonic() < deadline and child.poll() is None:
        time.sleep(0.1)
    if child.poll() is not None:
        _note(server, f"exited with code {child.returncode}")
        _forget_process(server, MCPServer.Status.FAILED)
        audit.record(audit.Action.MCP_STARTED, user=by, mcp_server=server, result=audit.Result.FAILURE, exit_code=child.returncode)
        raise RunnerError(f"It stopped straight away (exit code {child.returncode}). The Logs tab shows why.")

    _children[server.slug] = child
    try:
        create_time = psutil.Process(child.pid).create_time()
    except psutil.Error:
        create_time = time.time()
    _save(
        server,
        status=MCPServer.Status.RUNNING,
        health_status=MCPServer.HealthStatus.UNKNOWN,
        process_id=child.pid,
        process_create_time=create_time,
        started_at=timezone.now(),
    )
    audit.record(audit.Action.MCP_STARTED, user=by, mcp_server=server, pid=child.pid, port=manifest.port)
    return RuntimeState("running", pid=child.pid, started_at=server.started_at)


def stop(server: MCPServer, *, by) -> None:
    process = _our_process(server)
    if process is None:
        manifest = catalog.manifest_for(server)
        if manifest and port_in_use(manifest.port) and not server.process_id:
            raise RunnerError(
                "This copy was started outside the platform (for example in a terminal). Stop it there with Ctrl+C."
            )
        _forget_process(server, MCPServer.Status.STOPPED)
        return

    # The command (e.g. "uv run …") starts child processes; stop all of them.
    try:
        family = [*process.children(recursive=True), process]
    except psutil.Error:
        family = [process]
    for member in family:
        try:
            member.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(family, timeout=STOP_TIMEOUT_SECONDS)
    for member in alive:
        try:
            member.kill()
        except psutil.Error:
            pass

    child = _children.get(server.slug)
    if child is not None:
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    _note(server, f"stopped by {by.get_username() if by else 'platform'}")
    _forget_process(server, MCPServer.Status.STOPPED)
    audit.record(audit.Action.MCP_STOPPED, user=by, mcp_server=server)


def restart(server: MCPServer, *, by) -> RuntimeState:
    stop(server, by=by)
    return start(server, by=by)


def read_log(server: MCPServer) -> str:
    """The last part of the MCP's output."""
    path = log_path(server)
    try:
        with path.open("rb") as log:
            size = log.seek(0, os.SEEK_END)
            log.seek(max(0, size - LOG_TAIL_BYTES))
            data = log.read()
    except FileNotFoundError:
        return ""
    text = data.decode("utf-8", errors="replace")
    if len(data) == LOG_TAIL_BYTES and "\n" in text:
        text = text.split("\n", 1)[1]  # drop the partial first line
    return text
