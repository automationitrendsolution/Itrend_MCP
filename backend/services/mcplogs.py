"""
Turn an MCP's raw output (var/mcp_logs/<slug>.log) into something people can read.

The raw file mixes the platform's own notes ("=== … started by alice ==="),
the MCP's Python log lines, web-server (Uvicorn) lines, health checks and
tracebacks. parse() groups them into *runs* (one per Start) of clear events:

    Run started by alice at 15:17:10 · ran 9 s · stopped by alice
      15:17:12  INFO   Starting MySQL MCP 'mysql' on http://127.0.0.1:8101/mcp …
      15:17:12  INFO   Ready – accepting connections on http://127.0.0.1:8101
      15:17:30  WARN   A client was refused: missing or invalid token (401)

Routine noise (health checks, start-up chatter) is counted instead of listed.
The raw file is still available for download.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

PLATFORM_RE = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*) ===$")
PYLOG_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) (\S+) (.*)$")
UVICORN_RE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):\s+(.*)$")
ACCESS_RE = re.compile(r'^[\d.:\[\]a-f]+ - "(\w+) (\S+) HTTP/[\d.]+" (\d{3})(?: (.*))?$')

LEVELS = {"DEBUG": "debug", "INFO": "info", "WARNING": "warning", "ERROR": "error", "CRITICAL": "error"}

# Start-up chatter that says nothing useful to a reader.
HIDDEN = (
    re.compile(r"^Started server process"),
    re.compile(r"^Waiting for application (startup|shutdown)"),
    re.compile(r"^Application (startup|shutdown) complete"),
    re.compile(r"^Finished server process"),
    re.compile(r"StreamableHTTP session manager (started|shutting down)"),
)
# Technical messages rewritten in plain words.
REWRITES = (
    (re.compile(r"^Uvicorn running on (\S+).*$"), r"Ready – accepting connections on \1"),
    (re.compile(r"^Shutting down$"), "Shutting down"),
)
UNNAMED_ERROR = "The MCP hit an error"
STATUS_TEXT = {
    400: "the request was not valid",
    401: "missing or invalid token",
    403: "the token is not allowed here",
    404: "unknown address",
    405: "method not allowed",
    406: "the client sent the wrong headers",
    421: "wrong host name",
    429: "too many requests",
}


@dataclass
class LogEvent:
    time: str  # "15:17:12"
    level: str  # info | warning | error | debug
    message: str
    details: list[str] = field(default_factory=list)  # e.g. traceback lines

    @property
    def details_text(self) -> str:
        return "\n".join(self.details)


@dataclass
class LogRun:
    started_at: datetime | None = None
    started_by: str = ""
    command: str = ""
    ended_at: datetime | None = None
    ended_how: str = ""  # "Stopped by alice", "Exited with code 3", …
    ended_level: str = "info"
    events: list[LogEvent] = field(default_factory=list)
    health_ok: int = 0
    health_failed: int = 0
    requests: int = 0

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def duration(self) -> str:
        if not self.started_at or not self.ended_at:
            return ""
        seconds = int((self.ended_at - self.started_at).total_seconds())
        if seconds < 60:
            return f"{seconds} s"
        if seconds < 3600:
            return f"{seconds // 60} min {seconds % 60} s"
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"

    @property
    def warnings(self) -> int:
        return sum(1 for e in self.events if e.level == "warning")

    @property
    def errors(self) -> int:
        return sum(1 for e in self.events if e.level == "error") + (self.ended_level == "error")


def _parse_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _friendly(message: str) -> str | None:
    """Plain-words version of a message, or None to hide it."""
    if any(pattern.search(message) for pattern in HIDDEN):
        return None
    for pattern, replacement in REWRITES:
        if pattern.match(message):
            return pattern.sub(replacement, message)
    return message


def parse(text: str) -> list[LogRun]:
    """Runs, oldest first."""
    runs: list[LogRun] = []
    run: LogRun | None = None
    last_time = ""

    def current() -> LogRun:
        nonlocal run
        if run is None:  # output from before the first platform note
            run = LogRun()
            runs.append(run)
        return run

    def add(level: str, message: str) -> None:
        current().events.append(LogEvent(last_time, level, message))

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        note = PLATFORM_RE.match(line)
        if note:
            when, what = _parse_time(note.group(1)), note.group(2)
            last_time = note.group(1)[11:]
            if what.startswith("started by "):
                who, _, command = what[len("started by "):].partition(": ")
                run = LogRun(started_at=when, started_by=who, command=command)
                runs.append(run)
            elif what.startswith("stopped by "):
                current().ended_at, current().ended_how = when, f"Stopped by {what[len('stopped by '):]}"
            elif what.startswith("exited with code"):
                r = current()
                r.ended_at, r.ended_how, r.ended_level = when, f"Could not start – {what}", "error"
            elif what == "process ended":
                r = current()
                if r.ended_at is None:
                    r.ended_at, r.ended_how, r.ended_level = when, "Stopped unexpectedly", "error"
            else:
                add("info", what[:1].upper() + what[1:])
            continue

        pylog = PYLOG_RE.match(line)
        if pylog:
            last_time = pylog.group(1)[11:]
            message = _friendly(pylog.group(4))
            if message:
                add(LEVELS[pylog.group(2)], message)
            continue

        uvicorn = UVICORN_RE.match(line)
        if uvicorn:
            level, message = LEVELS[uvicorn.group(1)], uvicorn.group(2)
            access = ACCESS_RE.match(message)
            if access:
                _request(current(), last_time, access.group(1), access.group(2), int(access.group(3)))
                continue
            message = _friendly(message)
            if message:
                add(level, message)
            continue

        # Anything else: a traceback or a plain print(). Tracebacks belong to the line before.
        r = current()
        if line.startswith(("Traceback", "  ", "\t")) or (r.events and r.events[-1].details):
            previous = r.events[-1] if r.events else None
            if line.startswith("Traceback"):
                # Usually follows "ERROR … Query failed": attach it there instead of making a second error.
                if previous is None or previous.level != "error" or previous.details:
                    add("error", UNNAMED_ERROR)
            elif previous is None:
                add("info", line)
                continue
            event = r.events[-1]
            event.details.append(line)
            if not line.startswith((" ", "\t", "Traceback")):  # last line: "SomeError: message"
                event.message = f"Error: {line}" if event.message == UNNAMED_ERROR else f"{event.message} – {line}"
                event.level = "error"
        else:
            add("info", line)

    # A run with no end note is still running (or the website lost track of it).
    return runs


def _request(run: LogRun, time: str, method: str, path: str, status: int) -> None:
    if path.rstrip("/").endswith("/health"):
        if status == 200:
            run.health_ok += 1
        else:
            run.health_failed += 1
            run.events.append(LogEvent(time, "warning", f"Health check failed ({status})"))
        return
    run.requests += 1
    if status >= 500:
        run.events.append(LogEvent(time, "error", f"The MCP failed to answer a request ({status} on {method} {path})"))
    elif status >= 400:
        reason = STATUS_TEXT.get(status, "the request was refused")
        run.events.append(LogEvent(time, "warning", f"A client was refused: {reason} ({status})"))


def summarize(runs: list[LogRun], *, problems_only: bool = False) -> list[LogRun]:
    """Newest run first, optionally keeping only warnings and errors."""
    result = list(reversed(runs))
    if problems_only:
        for run in result:
            run.events = [e for e in run.events if e.level in {"warning", "error"}]
    return result
