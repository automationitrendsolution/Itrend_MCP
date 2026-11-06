"""
Your own MCPs, added through code.

Each MCP lives in its own folder under settings.MCP_SERVERS_DIR (mcp_servers/)
with an `mcp.json` file describing it:

    {
      "name": "MySQL MCP",
      "description": "Read-only access to the demo MySQL database.",
      "command": ["uv", "run", "mysql-mcp"],
      "port": 8101
    }

Optional keys: "mcp_path" (default "/mcp"), "health_path" (default "/health",
null to only check that the port answers) and "manual" (default "README.md").

The folder name is the MCP's slug. sync() creates or updates one registry
entry per folder. Name and description always come from mcp.json; who has
access is managed on the website.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction

from backend.models import MCPServer
from backend.validators import slug_format_validator, validate_slug_not_reserved

from . import audit

logger = logging.getLogger(__name__)

MANIFEST_NAME = "mcp.json"
SYNC_CACHE_KEY = "mcp-catalog:last-sync"
ALLOWED_KEYS = {"name", "description", "command", "port", "mcp_path", "health_path", "manual"}
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class Manifest:
    slug: str
    folder: Path
    name: str
    description: str
    command: tuple[str, ...]
    port: int
    mcp_path: str = "/mcp"
    health_path: str | None = "/health"
    manual: str = "README.md"

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.mcp_path}"

    @property
    def health_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}{self.health_path}" if self.health_path else None

    @property
    def manual_path(self) -> Path | None:
        """The manual file, only if it really is inside the MCP's folder."""
        path = (self.folder / self.manual).resolve()
        if not path.is_relative_to(self.folder.resolve()) or not path.is_file():
            return None
        return path


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def servers_dir() -> Path:
    return Path(settings.MCP_SERVERS_DIR)


def load_manifest(folder: Path) -> Manifest:
    """Read and check folder/mcp.json. Raises ManifestError with a readable message."""
    slug = folder.name
    try:
        slug_format_validator(slug)
        validate_slug_not_reserved(slug)
    except ValidationError as exc:
        raise ManifestError(f"Folder name “{slug}” cannot be used as a slug: {exc.messages[0]}") from None

    try:
        data = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"{slug}: no {MANIFEST_NAME} file.") from None
    except (OSError, ValueError) as exc:
        raise ManifestError(f"{slug}/{MANIFEST_NAME} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ManifestError(f"{slug}/{MANIFEST_NAME} must contain a JSON object.")

    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        raise ManifestError(f"{slug}/{MANIFEST_NAME}: unknown keys {', '.join(sorted(unknown))}.")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ManifestError(f"{slug}/{MANIFEST_NAME}: \"name\" is required (text, at most 100 characters).")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ManifestError(f"{slug}/{MANIFEST_NAME}: \"description\" must be text.")

    command = data.get("command")
    if not (isinstance(command, list) and command and all(isinstance(c, str) and c for c in command)):
        raise ManifestError(f"{slug}/{MANIFEST_NAME}: \"command\" must be a list such as [\"uv\", \"run\", \"my-mcp\"].")

    port = data.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ManifestError(f"{slug}/{MANIFEST_NAME}: \"port\" must be a number between 1024 and 65535.")

    mcp_path = data.get("mcp_path", "/mcp")
    health_path = data.get("health_path", "/health")
    for key, value in (("mcp_path", mcp_path), ("health_path", health_path)):
        if value is None and key == "health_path":
            continue
        if not isinstance(value, str) or not PATH_RE.match(value):
            raise ManifestError(f"{slug}/{MANIFEST_NAME}: \"{key}\" must start with / (for example \"/mcp\").")

    manual = data.get("manual", "README.md")
    if not isinstance(manual, str) or not manual:
        raise ManifestError(f"{slug}/{MANIFEST_NAME}: \"manual\" must be a file name such as \"README.md\".")

    return Manifest(
        slug=slug,
        folder=folder,
        name=name.strip(),
        description=description.strip(),
        command=tuple(command),
        port=port,
        mcp_path=mcp_path,
        health_path=health_path,
        manual=manual,
    )


def discover() -> tuple[list[Manifest], list[str]]:
    """All valid manifests under MCP_SERVERS_DIR, plus a message for each problem found."""
    root = servers_dir()
    manifests: list[Manifest] = []
    errors: list[str] = []
    if not root.is_dir():
        return manifests, [f"MCP folder {root} does not exist."]

    ports: dict[int, str] = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and (p / MANIFEST_NAME).exists()):
        try:
            manifest = load_manifest(folder)
        except ManifestError as exc:
            errors.append(str(exc))
            continue
        if manifest.port in ports:
            errors.append(f"{manifest.slug}: port {manifest.port} is already used by {ports[manifest.port]}.")
            continue
        ports[manifest.port] = manifest.slug
        manifests.append(manifest)
    return manifests, errors


def manifest_for(server: MCPServer) -> Manifest | None:
    """The current mcp.json of a local MCP, or None if its folder or file is gone or invalid."""
    if not server.is_local or not server.source_dir:
        return None
    try:
        return load_manifest(servers_dir() / server.source_dir)
    except ManifestError:
        return None


def manifest_problem(server: MCPServer) -> str:
    """Why manifest_for() returned None, for showing to administrators."""
    try:
        load_manifest(servers_dir() / server.source_dir)
    except ManifestError as exc:
        return str(exc)
    return ""


@transaction.atomic
def _apply(manifest: Manifest, report: SyncReport, by) -> None:
    server = MCPServer.objects.select_for_update().filter(slug=manifest.slug).first()
    wanted = {
        "name": manifest.name,
        "description": manifest.description,
        "deployment_type": MCPServer.DeploymentType.LOCAL,
        "endpoint": "",
        "internal_endpoint": manifest.local_url,
        "source_dir": manifest.slug,
    }
    if server is None:
        server = MCPServer(
            slug=manifest.slug,
            access_policy=MCPServer.AccessPolicy.RESTRICTED,
            status=MCPServer.Status.STOPPED,
            created_by=by,
            **wanted,
        )
        server.full_clean()
        server.save()
        audit.record(audit.Action.MCP_CREATED, user=by, mcp_server=server, name=server.name, source=MANIFEST_NAME)
        report.created.append(manifest.slug)
        return

    changed = sorted(key for key, value in wanted.items() if getattr(server, key) != value)
    if not changed:
        return
    for key in changed:
        setattr(server, key, wanted[key])
    if server.status == MCPServer.Status.PENDING:
        server.status = MCPServer.Status.STOPPED
    server.full_clean()
    server.save()
    audit.record(audit.Action.MCP_UPDATED, user=by, mcp_server=server, changed_fields=changed, source=MANIFEST_NAME)
    report.updated.append(manifest.slug)


def sync(*, by=None) -> SyncReport:
    """Create or update a registry entry for every MCP folder. Folders are never deleted from here."""
    manifests, errors = discover()
    report = SyncReport(errors=errors)
    for manifest in manifests:
        try:
            _apply(manifest, report, by)
        except ValidationError as exc:
            report.errors.append(f"{manifest.slug}: {'; '.join(exc.messages)}")
    cache.set(SYNC_CACHE_KEY, True, timeout=max(settings.MCP_DISCOVERY_INTERVAL, 1))
    if report.created or report.updated:
        logger.info("MCP folders synced: created %s, updated %s", report.created, report.updated)
    for error in report.errors:
        logger.warning("MCP folder problem: %s", error)
    return report


def sync_if_due() -> None:
    """Called on page loads: rescan the folders at most every MCP_DISCOVERY_INTERVAL seconds."""
    if settings.MCP_DISCOVERY_INTERVAL <= 0 or cache.get(SYNC_CACHE_KEY):
        return
    try:
        sync()
    except Exception:  # A broken folder must never break the website.
        logger.exception("Scanning the MCP folders failed")
