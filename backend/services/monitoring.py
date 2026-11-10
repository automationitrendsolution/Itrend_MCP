"""
MCP monitoring (Phase 6).

The monitor worker (manage.py monitor_mcps) calls run_once() every few seconds.
For every MCP whose check interval has passed, check_server():

    1. asks the runner what is really happening (running / crashed / stopped),
    2. performs a *real* MCP check, not just "is the process alive":
         - GET its /health address (from mcp.json), which must answer 200, and
         - POST an MCP "initialize" message to its MCP address, which must
           answer like an MCP server: 200 (session started, closed again
           straight away) or 401 with a Bearer challenge (it is up and wants a token),
    3. measures CPU and RAM of the MCP's whole process tree,
    4. stores the result (MCPHealthCheck) and records incidents (MCPMonitorEvent)
       only when something *changes*,
    5. restarts the MCP when its settings say so, within a safe limit.

Safety rules for automatic restarts:
    - an MCP an administrator stopped is never started again;
    - an MCP started outside the platform (in a terminal) is never touched;
    - at most max_restarts_per_hour attempts per hour, then the monitor stops
      trying and raises an alert ("Automatic restarts paused");
    - checks made before the last restart attempt do not count again, so one
      bad spell cannot cause a burst of restarts.

Each MCP is handled separately and every error is caught, so one broken MCP
never affects the website or the other MCPs.
"""

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import psutil
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from backend.models import MCPHealthCheck, MCPMonitorEvent, MCPMonitorSettings, MCPRequestStat, MCPServer

from . import catalog, runner
from .traffic import limits_for

logger = logging.getLogger(__name__)

Status = MCPHealthCheck.Status
Kind = MCPMonitorEvent.Kind
Level = MCPMonitorEvent.Level

PROBE_TIMEOUT = httpx.Timeout(5.0)
RESTART_ATTEMPTS = (Kind.AUTO_RESTARTED, Kind.RESTART_FAILED)
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": "itrend-monitor",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "itrend-monitor", "version": "1.0"},
    },
}


# ---------------------------------------------------------------------------
# The MCP check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    response_time_ms: float | None
    error: str = ""


def _probe_client() -> httpx.Client:
    return httpx.Client(timeout=PROBE_TIMEOUT, follow_redirects=False, trust_env=False)


def probe(manifest: catalog.Manifest, *, client: httpx.Client | None = None) -> ProbeResult:
    """Does this MCP really answer? Only local addresses from mcp.json are contacted."""
    own_client = client is None
    client = client or _probe_client()
    started = time.perf_counter()
    try:
        if manifest.health_url:
            health = client.get(manifest.health_url)
            if health.status_code != 200:
                return ProbeResult(False, _elapsed(started), f"Its health address answered HTTP {health.status_code}.")

        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        with client.stream("POST", manifest.local_url, json=INITIALIZE, headers=headers) as answer:
            # Status and headers are enough; the body (possibly an event stream) is not read.
            status, session = answer.status_code, answer.headers.get("mcp-session-id")
            challenge = answer.headers.get("www-authenticate", "")
        elapsed = _elapsed(started)
        if session:
            _close_session(client, manifest, session)
        if status == 200:
            return ProbeResult(True, elapsed)
        if status == 401 and challenge.lower().startswith("bearer"):
            return ProbeResult(True, elapsed)  # up, and correctly asking for a token
        return ProbeResult(False, elapsed, f"Its MCP address answered HTTP {status} instead of an MCP response.")
    except httpx.TimeoutException:
        return ProbeResult(False, _elapsed(started), f"It did not answer within {PROBE_TIMEOUT.read:.0f} seconds.")
    except httpx.HTTPError:
        return ProbeResult(False, None, f"Nothing is answering on port {manifest.port}.")
    finally:
        if own_client:
            client.close()


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _close_session(client: httpx.Client, manifest: catalog.Manifest, session: str) -> None:
    """End the session the check opened, so checks do not pile up sessions in the MCP."""
    try:
        client.delete(manifest.local_url, headers={"mcp-session-id": session})
    except httpx.HTTPError:
        pass


# ---------------------------------------------------------------------------
# CPU and memory
# ---------------------------------------------------------------------------


class ResourceSampler:
    """
    Measures CPU and RAM of an MCP's process tree ("uv run …" starts child processes).

    CPU use is measured between two samples, so the first sample of a process
    has no CPU value. The sampler is kept for the worker's whole life.
    """

    def __init__(self) -> None:
        self._known: dict[tuple[int, float], psutil.Process] = {}
        self._cpus = psutil.cpu_count() or 1

    def sample(self, pid: int) -> tuple[float | None, float | None]:
        try:
            root = psutil.Process(pid)
            family = [root, *root.children(recursive=True)]
        except psutil.Error:
            return None, None

        cpu_total, memory, complete = 0.0, 0, True
        for process in family:
            try:
                key = (process.pid, process.create_time())
                known = self._known.get(key)
                if known is None:
                    self._known[key] = process
                    process.cpu_percent(None)  # starts the measurement
                    complete = False
                else:
                    cpu_total += known.cpu_percent(None)
                memory += process.memory_info().rss
            except psutil.Error:
                complete = False
        self._forget_dead()
        cpu = round(cpu_total / self._cpus, 1) if complete else None
        return cpu, round(memory / (1024 * 1024), 1)

    def _forget_dead(self) -> None:
        for key, process in list(self._known.items()):
            if not process.is_running():
                del self._known[key]


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------


# A check older than this is not shown as the current health (e.g. the monitor stopped).
STALE_AFTER = timedelta(minutes=10)
LIVE_HEALTH = {Status.HEALTHY, Status.UNHEALTHY}


def health_of(server: MCPServer, last_status: str | None, last_checked_at: datetime | None) -> str:
    """
    The health to show for an MCP right now, from its state and its latest check:

        not_monitored  the platform does not run it
        stopped        stopped on purpose (an old check would mislead)
        down           stopped unexpectedly
        no_data        running, never checked
        stale          running, but the last check is older than STALE_AFTER
        healthy / unhealthy   the latest check, when it is recent
    """
    if not server.is_local:
        return "not_monitored"
    if server.status in {MCPServer.Status.STOPPED, MCPServer.Status.PENDING}:
        return "stopped"
    if server.status == MCPServer.Status.FAILED:
        return "down"
    if not last_status or last_checked_at is None:
        return "no_data"
    if last_checked_at < timezone.now() - STALE_AFTER:
        return "stale"
    return last_status


def last_check(server: MCPServer) -> MCPHealthCheck | None:
    return MCPHealthCheck.objects.filter(mcp_server=server).first()


def is_due(server: MCPServer, limits: MCPMonitorSettings, now: datetime) -> bool:
    previous = last_check(server)
    return previous is None or previous.checked_at <= now - timedelta(seconds=limits.health_check_interval_seconds)


def _event(server: MCPServer, kind: str, level: str, message: str) -> MCPMonitorEvent:
    logger.log(logging.WARNING if level != Level.INFO else logging.INFO, "monitor %s %s: %s", server.slug, kind, message)
    return MCPMonitorEvent.objects.create(mcp_server=server, kind=kind, level=level, message=message[:500])


def _last_attempt_time(server: MCPServer) -> datetime | None:
    event = MCPMonitorEvent.objects.filter(mcp_server=server, kind__in=RESTART_ATTEMPTS).first()
    return event.created_at if event else None


def _checks_since_last_attempt(server: MCPServer, count: int) -> list[MCPHealthCheck]:
    checks = MCPHealthCheck.objects.filter(mcp_server=server)
    since = _last_attempt_time(server)
    if since is not None:
        checks = checks.filter(checked_at__gt=since)
    return list(checks[:count])


def check_server(server: MCPServer, sampler: ResourceSampler, *, client: httpx.Client | None = None) -> MCPHealthCheck | None:
    """Check one MCP now. Returns None for MCPs that are not supposed to be running."""
    server.refresh_from_db()  # the website may have started or stopped it a moment ago
    manifest = catalog.manifest_for(server)
    if manifest is None:
        return None
    limits = limits_for(server)
    previous = last_check(server)
    state = runner.get_state(server)

    if state.state in {"stopped", "unavailable"}:
        return None  # stopped by an administrator, or never started: nothing to watch

    if state.state == "crashed":
        check = MCPHealthCheck.objects.create(
            mcp_server=server, status=Status.DOWN, error_message="The process stopped although nobody stopped it."
        )
        if previous is None or previous.status != Status.DOWN:
            _event(server, Kind.WENT_DOWN, Level.ERROR, f"{server.name} stopped unexpectedly. The Logs tab shows its last output.")
        _maybe_restart(server, limits, state, "it had stopped unexpectedly")
        return check

    result = probe(manifest, client=client)
    cpu, memory = sampler.sample(state.pid) if state.pid else (None, None)
    check = MCPHealthCheck.objects.create(
        mcp_server=server,
        status=Status.HEALTHY if result.ok else Status.UNHEALTHY,
        response_time_ms=result.response_time_ms,
        error_message=result.error[:500],
        cpu_percent=cpu,
        memory_mb=memory,
    )

    if result.ok and previous is not None and previous.status != Status.HEALTHY:
        _event(server, Kind.RECOVERED, Level.INFO, f"{server.name} is answering again.")
    if not result.ok and (previous is None or previous.status == Status.HEALTHY):
        _event(server, Kind.UNHEALTHY, Level.WARNING, f"{server.name} is running but not answering properly: {result.error}")

    if state.state != "running":
        return check  # started outside the platform: watched, never restarted

    _check_cpu(server, limits, check, previous)
    if not result.ok:
        recent = _checks_since_last_attempt(server, limits.restart_after_failed_checks)
        if len(recent) == limits.restart_after_failed_checks and all(c.status == Status.UNHEALTHY for c in recent):
            _maybe_restart(server, limits, state, f"it failed {len(recent)} checks in a row")
    elif limits.memory_limit_mb and memory is not None and memory > limits.memory_limit_mb:
        recent = _checks_since_last_attempt(server, 2)
        over = [c for c in recent if c.memory_mb is not None and c.memory_mb > limits.memory_limit_mb]
        if len(over) == 2:
            _event(
                server, Kind.MEMORY_LIMIT, Level.WARNING,
                f"{server.name} used {memory:.0f} MB, more than its limit of {limits.memory_limit_mb} MB, twice in a row.",
            )
            _maybe_restart(server, limits, state, "it stayed over its memory limit")
    return check


def _check_cpu(server: MCPServer, limits: MCPMonitorSettings, check: MCPHealthCheck, previous: MCPHealthCheck | None) -> None:
    limit = limits.cpu_limit_percent
    if not limit or check.cpu_percent is None or check.cpu_percent <= limit:
        return
    if previous is not None and previous.cpu_percent is not None and previous.cpu_percent > limit:
        return  # already reported; only the moment it goes over is recorded
    _event(server, Kind.CPU_LIMIT, Level.WARNING, f"{server.name} is using {check.cpu_percent:.0f}% of the server's CPU (limit {limit}%).")


def _maybe_restart(server: MCPServer, limits: MCPMonitorSettings, state: runner.RuntimeState, reason: str) -> None:
    if not limits.auto_restart:
        return
    hour_ago = timezone.now() - timedelta(hours=1)
    attempts = MCPMonitorEvent.objects.filter(mcp_server=server, kind__in=RESTART_ATTEMPTS, created_at__gte=hour_ago)
    if attempts.count() >= limits.max_restarts_per_hour:
        last_attempt = attempts.first()
        already_paused = MCPMonitorEvent.objects.filter(
            mcp_server=server, kind=Kind.GAVE_UP, created_at__gte=last_attempt.created_at
        ).exists()
        if not already_paused:
            _event(
                server, Kind.GAVE_UP, Level.ERROR,
                f"{server.name} was restarted {limits.max_restarts_per_hour} times in the last hour and still has problems. "
                "Automatic restarts are paused for now; please look at its logs.",
            )
        return

    try:
        if state.state == "running":
            runner.restart(server, by=None)
        else:
            runner.start(server, by=None)
    except runner.RunnerError as exc:
        _event(server, Kind.RESTART_FAILED, Level.ERROR, f"Could not restart {server.name} automatically ({reason}): {exc}")
    else:
        _event(server, Kind.AUTO_RESTARTED, Level.WARNING, f"{server.name} was restarted automatically because {reason}.")


# ---------------------------------------------------------------------------
# The worker's round
# ---------------------------------------------------------------------------


def monitored_servers():
    return MCPServer.objects.filter(deployment_type=MCPServer.DeploymentType.LOCAL)


def run_once(sampler: ResourceSampler, *, force: bool = False) -> list[MCPHealthCheck]:
    """Check every MCP that is due (or all of them with force=True)."""
    now = timezone.now()
    checks = []
    for server in monitored_servers():
        try:
            if not force and not is_due(server, limits_for(server), now):
                continue
            check = check_server(server, sampler)
            if check is not None:
                checks.append(check)
        except Exception:  # one broken MCP must not stop the others from being watched
            logger.exception("Monitoring %s failed", server.slug)
    touch_heartbeat()
    return checks


def prime(sampler: ResourceSampler) -> None:
    """Start CPU measurement of every running MCP, so the next sample has a CPU value."""
    for server in monitored_servers().filter(process_id__isnull=False):
        sampler.sample(server.process_id)


def purge_old_data(days: int | None = None) -> int:
    """Delete monitoring data older than MONITORING_RETENTION_DAYS. Returns the number of rows removed."""
    cutoff = timezone.now() - timedelta(days=days or settings.MONITORING_RETENTION_DAYS)
    removed = MCPHealthCheck.objects.filter(checked_at__lt=cutoff).delete()[0]
    removed += MCPRequestStat.objects.filter(bucket_start__lt=cutoff).delete()[0]
    removed += MCPMonitorEvent.objects.filter(created_at__lt=cutoff).delete()[0]
    return removed


# ---------------------------------------------------------------------------
# Is the worker running?
# ---------------------------------------------------------------------------


def touch_heartbeat() -> None:
    path = Path(settings.MONITOR_HEARTBEAT_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        logger.warning("Could not write the monitor heartbeat file %s", path)


def worker_last_seen() -> datetime | None:
    try:
        return datetime.fromtimestamp(Path(settings.MONITOR_HEARTBEAT_FILE).stat().st_mtime, tz=timezone.get_current_timezone())
    except OSError:
        return None


def worker_is_running() -> bool:
    seen = worker_last_seen()
    allowed = max(settings.MONITOR_TICK_SECONDS * 3, 30)
    return seen is not None and timezone.now() - seen < timedelta(seconds=allowed)


# ---------------------------------------------------------------------------
# Figures for the pages and the API
# ---------------------------------------------------------------------------


def summary(server: MCPServer, *, hours: int = 24) -> dict:
    """Last check plus the last `hours` of checks, restarts and traffic for one MCP."""
    from .traffic import totals

    since = timezone.now() - timedelta(hours=hours)
    checks = MCPHealthCheck.objects.filter(mcp_server=server, checked_at__gte=since).aggregate(
        total=Count("id"), healthy=Count("id", filter=Q(status=Status.HEALTHY))
    )
    restarts = MCPMonitorEvent.objects.filter(mcp_server=server, kind=Kind.AUTO_RESTARTED, created_at__gte=since).count()
    return {
        "last": last_check(server),
        "checks": checks["total"],
        "uptime_percent": round(100 * checks["healthy"] / checks["total"], 1) if checks["total"] else None,
        "auto_restarts": restarts,
        "traffic": totals(server, hours=hours),
    }
