"""
Per-part access inside one MCP, e.g. which databases of the MySQL MCP a person may use.

An MCP opts in with "access_items_path" in its mcp.json. The platform reads the list of parts
from that address (only reachable on this machine), shows a switch per person and part on the
MCP's Access page, and sends each person's parts with every token check:
    {"items": ["powerbi"]}     or     {"items": ["*"]} for superusers (everything).
Nobody gets a part until it is switched on for them.
"""

import json
import logging
import re
import urllib.error
import urllib.request

from django.core.exceptions import PermissionDenied
from django.db import transaction

from backend.models import MCPItemAccess, MCPServer

from . import access, audit, catalog

logger = logging.getLogger(__name__)

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_ITEMS = 100
ALL = "*"


def uses_items(server: MCPServer) -> bool:
    manifest = catalog.manifest_for(server)
    return bool(manifest and manifest.access_items_path)


def items_of(server: MCPServer) -> list[dict]:
    """The parts last reported by the MCP: [{"key": ..., "label": ...}]."""
    return list((server.access_items or {}).get("items") or [])


def items_label(server: MCPServer) -> str:
    return (server.access_items or {}).get("label") or "Parts"


def _clean(data) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    items, seen = [], set()
    for raw in data["items"][:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key", ""))
        if not KEY_RE.match(key) or key in seen:
            continue
        seen.add(key)
        items.append({"key": key, "label": str(raw.get("label") or key)[:100]})
    return {"label": str(data.get("label") or "Parts")[:40], "items": items}


def refresh(server: MCPServer) -> bool:
    """Ask the running MCP for its parts and remember them. False if it could not be asked."""
    manifest = catalog.manifest_for(server)
    if not manifest or not manifest.access_items_url:
        return False
    try:
        with urllib.request.urlopen(manifest.access_items_url, timeout=2) as response:  # noqa: S310 - local URL from mcp.json
            data = _clean(json.loads(response.read(200_000)))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    if data is None:
        logger.warning("%s: %s did not return a valid list", server.slug, manifest.access_items_path)
        return False
    if data != server.access_items:
        server.access_items = data
        server.save(update_fields=["access_items"])
    return True


def allowed_items(user, server: MCPServer) -> list[str]:
    """What the token check tells the MCP. Superusers get everything."""
    if user.is_superuser:
        return [ALL]
    return sorted(MCPItemAccess.objects.filter(user=user, mcp_server=server).values_list("item", flat=True))


def switched_on(server: MCPServer) -> dict[int, set[str]]:
    """{user id: {item, …}} for the Access page."""
    result: dict[int, set[str]] = {}
    for user_id, item in MCPItemAccess.objects.filter(mcp_server=server).values_list("user_id", "item"):
        result.setdefault(user_id, set()).add(item)
    return result


@transaction.atomic
def set_item(*, server: MCPServer, user, item: str, on: bool, changed_by) -> None:
    if not access.can_admin(changed_by, server):
        raise PermissionDenied("You cannot change access to this MCP.")
    labels = {entry["key"]: entry["label"] for entry in items_of(server)}
    if item not in labels:
        raise PermissionDenied("Unknown part of this MCP.")
    if on:
        _, created = MCPItemAccess.objects.get_or_create(
            user=user, mcp_server=server, item=item, defaults={"granted_by": changed_by}
        )
        changed = created
    else:
        changed = MCPItemAccess.objects.filter(user=user, mcp_server=server, item=item).delete()[0] > 0
    if changed:
        audit.record(
            audit.Action.PERMISSION_GRANTED if on else audit.Action.PERMISSION_REVOKED,
            user=changed_by,
            mcp_server=server,
            target_user=user.get_username(),
            item=item,
            item_label=labels[item],
            item_kind=items_label(server).rstrip("s").lower() or "part",
        )


def forget_user(server: MCPServer, user) -> None:
    """Called when someone loses access to the MCP: their switches go too."""
    MCPItemAccess.objects.filter(user=user, mcp_server=server).delete()
