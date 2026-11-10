import logging
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from backend.models import (
    PERMISSION_LEVEL_HELP,
    AccessToken,
    MCPActivityLog,
    MCPHealthCheck,
    MCPMonitorEvent,
    MCPPermission,
    MCPRequestStat,
    MCPServer,
    PermissionLevel,
)
from backend.permissions import can_manage_mcps
from backend.services import access, accounts, audit, catalog, mcplogs, monitoring, registry, runner, tokens, traffic
from backend.services.health import check_platform

from .markdown import render_markdown_file
from .forms import (
    AccessTokenForm,
    LoginForm,
    MonitorSettingsForm,
    PermissionGrantForm,
    ServiceAccountForm,
)

logger = logging.getLogger(__name__)

MCP_PAGE_SIZE = 20
USER_PAGE_SIZE = 25
PERMISSION_PAGE_SIZE = 25
ACTIVITY_PAGE_SIZE = 50
USER_ACTIVITY_SHOWN = 10
MCP_EVENTS_SHOWN = 50
MCP_RUNS_SHOWN = 10


def staff_required(view):
    """Logged-in administrators only. Others get a 403 page."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not can_manage_mcps(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def redirect_back(request: HttpRequest, default: str) -> HttpResponse:
    """Redirect to the ?next= URL if it is a safe local address, otherwise to `default`."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(default)


def visible_server_or_404(request: HttpRequest, pk: int) -> MCPServer:
    # Servers the user may not see return 404, so their existence is not revealed.
    return get_object_or_404(MCPServer.objects.visible_to(request.user), pk=pk)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        logger.info("User %r logged in", form.get_user().get_username())
        return super().form_valid(form)


class LogoutView(auth_views.LogoutView):
    """Logs the user out. Django only accepts POST here, which prevents logout via CSRF links."""


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    """Your account, your access at a glance, and display preferences (theme)."""
    context = {
        "my_permissions": access.list_permissions(user=request.user),
        "my_active_tokens": tokens.list_tokens(user=request.user).filter(revoked_at__isnull=True, expires_at__gt=timezone.now()).count(),
    }
    return render(request, "accounts/profile.html", context)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


OVERVIEW_SERVERS_SHOWN = 8
OVERVIEW_ACTIVITY_SHOWN = 8


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Overview. Every figure comes from the database or a real check; nothing is estimated."""
    user = request.user
    is_admin = can_manage_mcps(user)
    servers = list(with_last_check(registry.list_servers(user)).order_by("name")[:OVERVIEW_SERVERS_SHOWN])
    context = {
        "counts": registry.get_counts(user),
        "active_user_count": get_user_model().objects.filter(is_active=True).count(),
        "servers": servers,
        "is_admin": is_admin,
        "my_active_tokens": tokens.list_tokens(user=user).filter(revoked_at__isnull=True, expires_at__gt=timezone.now()).count(),
        "can_create_token": not access.is_service_account(user),
    }
    if is_admin:
        traffic_24h = traffic.totals()
        if traffic_24h["requests"]:
            traffic_24h["error_rate"] = round(100 * traffic_24h["errors"] / traffic_24h["requests"], 1)
        context.update(
            {
                "traffic": traffic_24h,
                "warnings": _overview_warnings(),
                "activity": MCPActivityLog.objects.select_related("mcp_server")[:OVERVIEW_ACTIVITY_SHOWN],
            }
        )
    return render(request, "dashboard/index.html", context)


def with_last_check(servers):
    """Add each MCP's latest monitoring check (last_check_status, last_check_at) in the same query."""
    latest = MCPHealthCheck.objects.filter(mcp_server=OuterRef("pk")).order_by("-checked_at", "-id")
    return servers.annotate(
        last_check_status=Subquery(latest.values("status")[:1]),
        last_check_at=Subquery(latest.values("checked_at")[:1]),
    )


def _overview_warnings() -> list[dict]:
    """Things an administrator should act on, most serious first. Empty when all is well."""
    warnings = []
    for server in MCPServer.objects.filter(status=MCPServer.Status.FAILED).order_by("name"):
        warnings.append({"level": "error", "text": f"{server.name} stopped unexpectedly.", "url": reverse("frontend:mcp-logs", args=[server.pk]), "action": "View logs"})
    day_ago = timezone.now() - timedelta(hours=24)
    for event in MCPMonitorEvent.objects.filter(kind=MCPMonitorEvent.Kind.GAVE_UP, created_at__gte=day_ago).select_related("mcp_server")[:3]:
        warnings.append({"level": "error", "text": event.message, "url": reverse("frontend:monitoring-detail", args=[event.mcp_server_id]), "action": "Monitoring"})
    running = with_last_check(MCPServer.objects.filter(status=MCPServer.Status.RUNNING)).order_by("name")
    for server in running:
        if server.last_check_status in {MCPHealthCheck.Status.UNHEALTHY, MCPHealthCheck.Status.DOWN}:
            warnings.append({"level": "warning", "text": f"{server.name} is running but not answering properly.", "url": reverse("frontend:monitoring-detail", args=[server.pk]), "action": "Monitoring"})
    if running and not monitoring.worker_is_running():
        warnings.append({"level": "warning", "text": "The monitor is not running: no health checks or automatic restarts.", "url": reverse("frontend:monitoring"), "action": "Details"})
    _, folder_problems = catalog.discover()
    for problem in folder_problems[:3]:
        warnings.append({"level": "warning", "text": problem, "url": reverse("frontend:mcp-list"), "action": "Registry"})
    return warnings


@login_required
def system_status(request: HttpRequest) -> HttpResponse:
    """HTMX partial: refreshed periodically on the dashboard."""
    return render(request, "dashboard/partials/system_status.html", {"health": check_platform()})


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


@login_required
def mcp_list(request: HttpRequest) -> HttpResponse:
    filters = {
        "search": request.GET.get("q", ""),
        "status": request.GET.get("status", ""),
        "deployment_type": request.GET.get("type", ""),
    }
    sort = request.GET.get("sort", "name")
    if sort not in MCP_SORTS:
        sort = "name"
    servers = with_last_check(registry.list_servers(request.user, **filters)).order_by(*MCP_SORTS[sort])
    page = Paginator(servers, MCP_PAGE_SIZE).get_page(request.GET.get("page"))

    query = request.GET.copy()
    for key in ("page", "sort"):
        query.pop(key, None)
    context = {
        "page": page,
        "filters": filters,
        "sort": sort,
        "query": query.urlencode(),  # current search and filters, kept by sort and page links
        "status_choices": MCPServer.Status.choices,
        # Only offer types that some visible MCP really has; the others would always give an empty list.
        "type_choices": _used_choices(MCPServer.DeploymentType.choices, MCPServer.objects.visible_to(request.user), "deployment_type"),
        "can_manage": can_manage_mcps(request.user),
        "servers_dir": catalog.servers_dir(),
    }
    # HTMX search/filter/pagination only needs the table, not the whole page.
    template = "mcps/partials/table.html" if request.htmx else "mcps/list.html"
    return render(request, template, context)


# Registry sort options: ?sort=<key>. A leading "-" means newest/last first.
MCP_SORTS = {
    "name": ("name",),
    "-name": ("-name",),
    "status": ("status", "name"),
    "-status": ("-status", "name"),
    "created": ("created_at", "name"),
    "-created": ("-created_at", "name"),
    "checked": ("last_check_at", "name"),
    "-checked": ("-last_check_at", "name"),
}


def _used_choices(choices, queryset, field: str) -> list[tuple[str, str]]:
    used = set(queryset.values_list(field, flat=True).distinct())
    return [(value, label) for value, label in choices if value in used]


@require_POST
@staff_required
def mcp_rescan(request: HttpRequest) -> HttpResponse:
    """Look for new or changed mcp_servers/<slug>/mcp.json files now."""
    report = catalog.sync(by=request.user)
    for slug in report.created:
        messages.success(request, f"New MCP found: {slug}.")
    for slug in report.updated:
        messages.info(request, f"Updated from mcp.json: {slug}.")
    for error in report.errors:
        messages.error(request, error)
    if not (report.created or report.updated or report.errors):
        messages.info(request, "No changes found in the MCP folders.")
    return redirect_back(request, reverse("frontend:mcp-list"))


MCP_TABS = [
    ("overview", "Overview", "bi-info-circle", "frontend:mcp-detail"),
    ("logs", "Logs", "bi-terminal", "frontend:mcp-logs"),
    ("access", "Who has access", "bi-people", "frontend:mcp-access"),
    ("manual", "User manual", "bi-book", "frontend:mcp-manual"),
]


def _mcp_page(request: HttpRequest, pk: int, tab: str) -> tuple[MCPServer, dict]:
    """Shared by the four MCP tabs: the server, the visitor's rights, and the header state."""
    server = visible_server_or_404(request, pk)
    level = access.effective_level(request.user, server)
    is_admin = can_manage_mcps(request.user)
    can_admin_server = access.can_admin(request.user, server)
    allowed_tabs = {"overview", "manual"} | ({"logs", "access"} if can_admin_server else set())
    if tab not in allowed_tabs:
        raise PermissionDenied
    context = {
        "server": server,
        "tab": tab,
        "tabs": [t for t in MCP_TABS if t[0] in allowed_tabs],
        "level": PermissionLevel(level),
        "level_help": PERMISSION_LEVEL_HELP[level],
        "is_admin": is_admin,
        "can_admin_server": can_admin_server,
        "manifest": catalog.manifest_for(server),
        "runtime": runner.get_state(server),
    }
    return server, context


@login_required
def mcp_detail(request: HttpRequest, pk: int) -> HttpResponse:
    server, context = _mcp_page(request, pk, "overview")
    context.update(
        {
            "can_connect": access.can_connect(request.user, server) and not access.is_service_account(request.user),
            "my_tokens": tokens.list_tokens(user=request.user, server=server)[:20],
            "manifest_problem": catalog.manifest_problem(server) if server.is_local and not context["manifest"] else "",
            # A local MCP whose folder still exists would come straight back, so it cannot be deleted here.
            "can_delete": context["is_admin"] and not (server.is_local and context["manifest"]),
            "last_check": monitoring.last_check(server),
            "traffic": traffic.totals(server) if context["is_admin"] else None,
        }
    )
    last = context["last_check"]
    context["health_now"] = monitoring.health_of(server, last.status if last else None, last.checked_at if last else None)
    return render(request, "mcps/detail.html", context)


@login_required
def mcp_logs(request: HttpRequest, pk: int) -> HttpResponse:
    server, context = _mcp_page(request, pk, "logs")
    if context["is_admin"]:
        context.update(_server_log_context(request, server, context["runtime"]))
    context["events"] = MCPActivityLog.objects.filter(mcp_server=server).select_related("mcp_server")[:MCP_EVENTS_SHOWN]
    return render(request, "mcps/logs.html", context)


def _server_log_context(request: HttpRequest, server: MCPServer, runtime) -> dict:
    """The MCP's output as readable runs (newest first), for the Logs tab."""
    severity = request.GET.get("severity", "")
    if request.GET.get("problems") == "1" and not severity:  # older links: ?problems=1
        severity = "problems"
    if severity not in mcplogs.SEVERITIES:
        severity = ""
    search = request.GET.get("q", "").strip()[:100]
    runs = mcplogs.summarize(mcplogs.parse(runner.read_log(server)), severity=severity, search=search)
    return {
        "server": server,
        "runs": runs[:MCP_RUNS_SHOWN],
        "severity": severity,
        "search": search,
        "problems_only": bool(severity),  # some messages below still read this
        "runtime": runtime,
    }


@staff_required
def mcp_log_output(request: HttpRequest, pk: int) -> HttpResponse:
    """HTMX partial: the MCP's output, refreshed every few seconds and when the filter changes."""
    server = get_object_or_404(MCPServer, pk=pk)
    return render(request, "mcps/partials/log_runs.html", _server_log_context(request, server, runner.get_state(server)))


@staff_required
def mcp_log_download(request: HttpRequest, pk: int) -> HttpResponse:
    """The complete raw output file, for troubleshooting."""
    server = get_object_or_404(MCPServer, pk=pk)
    path = runner.log_path(server)
    if not path.exists():
        raise Http404("No output yet.")
    return FileResponse(path.open("rb"), as_attachment=True, filename=f"{server.slug}-output.log", content_type="text/plain")


@login_required
def mcp_access(request: HttpRequest, pk: int) -> HttpResponse:
    server, context = _mcp_page(request, pk, "access")
    context["permissions"] = access.list_permissions(server=server)
    context["grant_form"] = PermissionGrantForm(server=server)
    context["policy_choices"] = MCPServer.AccessPolicy.choices
    return render(request, "mcps/access.html", context)


@login_required
def mcp_manual(request: HttpRequest, pk: int) -> HttpResponse:
    server, context = _mcp_page(request, pk, "manual")
    manifest = context["manifest"]
    context["manual_html"] = render_markdown_file(manifest.manual_path) if manifest and manifest.manual_path else ""
    context["can_connect"] = access.can_connect(request.user, server) and not access.is_service_account(request.user)
    return render(request, "mcps/manual.html", context)


@login_required
def mcp_state(request: HttpRequest, pk: int) -> HttpResponse:
    """HTMX partial: running/stopped badge and the Start/Stop buttons, refreshed every few seconds."""
    server = visible_server_or_404(request, pk)
    context = {
        "server": server,
        "is_admin": can_manage_mcps(request.user),
        "manifest": catalog.manifest_for(server),
        "runtime": runner.get_state(server),
    }
    return render(request, "mcps/partials/state.html", context)


@require_POST
@staff_required
def mcp_control(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    """Start, stop or restart a local MCP. Administrators only."""
    server = get_object_or_404(MCPServer, pk=pk)
    operations = {"start": runner.start, "stop": runner.stop, "restart": runner.restart}
    if action not in operations:
        raise PermissionDenied
    try:
        operations[action](server, by=request.user)
    except runner.RunnerError as exc:
        messages.error(request, f"Could not {action} {server.name}: {exc}")
    else:
        done = {"start": "started", "stop": "stopped", "restart": "restarted"}[action]
        messages.success(request, f"{server.name} was {done}.")
    return redirect_back(request, reverse("frontend:mcp-detail", args=[server.pk]))


@require_POST
@staff_required
def mcp_access_policy(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(MCPServer, pk=pk)
    policy = request.POST.get("access_policy", "")
    if policy not in MCPServer.AccessPolicy.values:
        messages.error(request, "Choose who can use this MCP.")
    elif policy != server.access_policy:
        registry.update_server(server, updated_by=request.user, access_policy=policy)
        messages.success(request, f"{server.name}: {server.get_access_policy_display()}.")
    return redirect("frontend:mcp-access", pk=server.pk)


@require_POST
@staff_required
def mcp_delete(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(MCPServer, pk=pk)
    if server.is_local and catalog.manifest_for(server):
        messages.error(request, f"Delete the folder mcp_servers/{server.source_dir} first; otherwise {server.name} comes back.")
        return redirect("frontend:mcp-detail", pk=server.pk)
    name = server.name
    registry.delete_server(server, deleted_by=request.user)
    messages.success(request, f"MCP server “{name}” was removed from the platform.")
    return redirect("frontend:mcp-list")


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------


@login_required
def token_list(request: HttpRequest) -> HttpResponse:
    context = {
        "tokens": tokens.list_tokens(user=request.user),
        "can_create": not access.is_service_account(request.user),
    }
    return render(request, "tokens/list.html", context)


@never_cache
@login_required
def token_create(request: HttpRequest, user_pk: int | None = None) -> HttpResponse:
    """
    Generate a token. Without user_pk: for yourself. With user_pk: an administrator
    creating a token for a service account.

    The token is shown in the response to the POST, once. It is never stored
    in the session or anywhere else.
    """
    owner = request.user if user_pk is None else get_object_or_404(get_user_model(), pk=user_pk)
    if not tokens.can_create_for(request.user, owner):
        raise PermissionDenied

    servers = access.connectable_servers(owner)
    initial = {}
    if request.GET.get("mcp", "").isdigit():
        initial["mcp_server"] = int(request.GET["mcp"])
    form = AccessTokenForm(request.POST or None, servers=servers, initial=initial)

    if request.method == "POST" and form.is_valid():
        try:
            token, plaintext = tokens.create_token(
                owner=owner,
                server=form.cleaned_data["mcp_server"],
                name=form.cleaned_data["name"],
                expires_in_days=form.cleaned_data["expires_in_days"],
                created_by=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return render(request, "tokens/created.html", {"token": token, "plaintext": plaintext, "owner": owner})

    return render(request, "tokens/create.html", {"form": form, "owner": owner, "servers": servers})


@require_POST
@login_required
def token_revoke(request: HttpRequest, pk: int) -> HttpResponse:
    token = get_object_or_404(AccessToken.objects.select_related("user", "mcp_server"), pk=pk)
    if not tokens.can_revoke(request.user, token):
        raise PermissionDenied
    tokens.revoke_token(token, revoked_by=request.user)
    messages.success(request, f"Token “{token.name}” was revoked. It stops working immediately.")
    return redirect_back(request, reverse("frontend:token-list"))


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@staff_required
def permission_list(request: HttpRequest) -> HttpResponse:
    filters = {"mcp": request.GET.get("mcp", ""), "q": request.GET.get("q", "").strip()}
    permissions = access.list_permissions()
    if filters["mcp"].isdigit():
        permissions = permissions.filter(mcp_server_id=int(filters["mcp"]))
    if filters["q"]:
        permissions = permissions.filter(user__username__icontains=filters["q"])
    context = {
        "page": Paginator(permissions, PERMISSION_PAGE_SIZE).get_page(request.GET.get("page")),
        "filters": filters,
        "servers": MCPServer.objects.all(),
        "grant_form": PermissionGrantForm(),
    }
    return render(request, "permissions/list.html", context)


@require_POST
@login_required
def permission_grant(request: HttpRequest) -> HttpResponse:
    form = PermissionGrantForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect_back(request, reverse("frontend:dashboard"))

    server, user = form.cleaned_data["mcp_server"], form.cleaned_data["user"]
    if not access.can_admin(request.user, server):
        raise PermissionDenied
    level = form.cleaned_data["permission_level"]
    try:
        access.grant_permission(server=server, user=user, level=level, granted_by=request.user)
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    else:
        messages.success(request, f"{user.get_username()} now has {PermissionLevel(level).label} on {server.name}.")
    return redirect_back(request, reverse("frontend:mcp-detail", args=[server.pk]))


@require_POST
@login_required
def permission_revoke(request: HttpRequest, pk: int) -> HttpResponse:
    permission = get_object_or_404(MCPPermission.objects.select_related("user", "mcp_server"), pk=pk)
    if not access.can_admin(request.user, permission.mcp_server):
        raise PermissionDenied
    access.revoke_permission(permission, revoked_by=request.user)
    messages.success(request, f"Removed {permission.user.get_username()}'s permission on {permission.mcp_server.name}.")
    return redirect_back(request, reverse("frontend:mcp-detail", args=[permission.mcp_server_id]))


# ---------------------------------------------------------------------------
# Users and service accounts (administrators)
# ---------------------------------------------------------------------------


@staff_required
def user_list(request: HttpRequest) -> HttpResponse:
    filters = {"q": request.GET.get("q", ""), "kind": request.GET.get("kind", "")}
    users = accounts.list_users(search=filters["q"], kind=filters["kind"])
    context = {"page": Paginator(users, USER_PAGE_SIZE).get_page(request.GET.get("page")), "filters": filters}
    return render(request, "users/list.html", context)


@staff_required
def user_detail(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(get_user_model().objects.select_related("service_account"), pk=pk)
    context = {
        "account": account,
        "is_service": access.is_service_account(account),
        "permissions": access.list_permissions(user=account),
        "tokens": tokens.list_tokens(user=account),
        "grant_form": PermissionGrantForm(user=account) if account.is_active else None,
        "can_create_token": tokens.can_create_for(request.user, account),
        # Access history: what this account did, and what was done to it (granted, deactivated…).
        "activity": MCPActivityLog.objects.filter(Q(user=account) | Q(metadata__target_user=account.get_username()))
        .select_related("mcp_server")[:USER_ACTIVITY_SHOWN],
    }
    return render(request, "users/detail.html", context)


@require_POST
@staff_required
def user_set_active(request: HttpRequest, pk: int) -> HttpResponse:
    account = get_object_or_404(get_user_model(), pk=pk)
    active = request.POST.get("active") == "1"
    try:
        accounts.set_active(account, active, changed_by=request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, f"{account.get_username()} was {'activated' if active else 'deactivated'}.")
    return redirect("frontend:user-detail", pk=account.pk)


@staff_required
def service_account_create(request: HttpRequest) -> HttpResponse:
    form = ServiceAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            account = accounts.create_service_account(created_by=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(
                request,
                f"Service account {account.user.get_username()} was created. Give it permissions, then generate a token.",
            )
            return redirect("frontend:user-detail", pk=account.user_id)
    return render(request, "users/service_account_create.html", {"form": form})


# ---------------------------------------------------------------------------
# Activity log (administrators)
# ---------------------------------------------------------------------------

# The chips at the top of the Activity page.
ACTIVITY_KINDS = {
    "signins": ("Sign-ins", "bi-box-arrow-in-right", ["login", "login_failed", "logout"]),
    "connections": ("Connections", "bi-plug", ["mcp_connection"]),
    "access": ("Access & tokens", "bi-key", ["permission_granted", "permission_revoked", "token_created", "token_revoked"]),
    "mcps": ("MCP servers", "bi-hdd-stack", ["mcp_created", "mcp_updated", "mcp_deleted", "mcp_started", "mcp_stopped"]),
    "users": ("Users", "bi-people", ["user_activated", "user_deactivated", "service_account_created"]),
}


# The "When" filter on the Audit Logs page: ?period=24h|7d|30d (empty = all time).
ACTIVITY_PERIODS = {
    "24h": ("Last 24 hours", timedelta(hours=24)),
    "7d": ("Last 7 days", timedelta(days=7)),
    "30d": ("Last 30 days", timedelta(days=30)),
}


@staff_required
def activity_list(request: HttpRequest) -> HttpResponse:
    filters = {
        "kind": request.GET.get("kind", ""),
        "problems": request.GET.get("problems") == "1",
        "q": request.GET.get("q", "").strip(),
        "period": request.GET.get("period", ""),
    }
    entries = MCPActivityLog.objects.select_related("mcp_server")
    if filters["period"] in ACTIVITY_PERIODS:
        entries = entries.filter(timestamp__gte=timezone.now() - ACTIVITY_PERIODS[filters["period"]][1])
    else:
        filters["period"] = ""
    if filters["kind"] in ACTIVITY_KINDS:
        entries = entries.filter(action__in=ACTIVITY_KINDS[filters["kind"]][2])
    if filters["problems"]:
        entries = entries.exclude(result=MCPActivityLog.Result.SUCCESS)
    if filters["q"]:
        entries = entries.filter(
            Q(username__icontains=filters["q"]) | Q(mcp_slug__icontains=filters["q"]) | Q(mcp_server__name__icontains=filters["q"])
        )

    since = timezone.now() - timedelta(hours=24)
    recent = MCPActivityLog.objects.filter(timestamp__gte=since).aggregate(
        connections=Count("id", filter=Q(action="mcp_connection", result="success")),
        refused=Count("id", filter=Q(action="mcp_connection") & ~Q(result="success")),
        failed_logins=Count("id", filter=Q(action="login_failed")),
        changes=Count("id", filter=Q(action__in=ACTIVITY_KINDS["access"][2] + ACTIVITY_KINDS["mcps"][2] + ACTIVITY_KINDS["users"][2])),
    )
    query = request.GET.copy()
    query.pop("page", None)
    context = {
        "page": Paginator(entries, ACTIVITY_PAGE_SIZE).get_page(request.GET.get("page")),
        "filters": filters,
        "kinds": [(key, label, icon) for key, (label, icon, _) in ACTIVITY_KINDS.items()],
        "periods": [(key, label) for key, (label, _) in ACTIVITY_PERIODS.items()],
        "recent": recent,
        "query": query.urlencode(),
    }
    template = "activity/partials/timeline.html" if request.htmx else "activity/list.html"
    return render(request, template, context)


# ---------------------------------------------------------------------------
# Monitoring (administrators, Phase 6)
# ---------------------------------------------------------------------------

MONITOR_EVENTS_SHOWN = 30
MONITOR_CHECKS_SHOWN = 20


# Time ranges offered on the Monitoring pages: ?range=1h|24h|7d (default 24h).
MONITOR_RANGES = {
    "1h": {"key": "1h", "hours": 1, "short": "1 h", "long": "last hour", "label": "1 hour"},
    "24h": {"key": "24h", "hours": 24, "short": "24 h", "long": "last 24 hours", "label": "24 hours"},
    "7d": {"key": "7d", "hours": 168, "short": "7 d", "long": "last 7 days", "label": "7 days"},
}


def _monitor_range(request: HttpRequest) -> dict:
    return MONITOR_RANGES.get(request.GET.get("range", ""), MONITOR_RANGES["24h"])


def _monitoring_row(server: MCPServer, hours: int = 24) -> dict:
    data = monitoring.summary(server, hours=hours)
    last = data["last"]
    health = monitoring.health_of(server, last.status if last else None, last.checked_at if last else None)
    # Response time, CPU and RAM are only shown from a recent check of a running MCP.
    return {"server": server, "health": health, "live": health in monitoring.LIVE_HEALTH, **data}


@staff_required
def monitoring_overview(request: HttpRequest) -> HttpResponse:
    period = _monitor_range(request)
    rows = [_monitoring_row(server, period["hours"]) for server in MCPServer.objects.order_by("name")]
    context = {
        "rows": rows,
        "range": period,
        "ranges": MONITOR_RANGES.values(),
        "counts": {
            "monitored": sum(1 for row in rows if row["server"].is_local),
            "healthy": sum(1 for row in rows if row["health"] == "healthy"),
            "problems": sum(1 for row in rows if row["health"] in {"unhealthy", "down"}),
        },
        "traffic": traffic.totals(hours=period["hours"]),
        "events": MCPMonitorEvent.objects.select_related("mcp_server")[:MONITOR_EVENTS_SHOWN],
        "worker_running": monitoring.worker_is_running(),
        "worker_last_seen": monitoring.worker_last_seen(),
    }
    # The live part refreshes itself every few seconds through HTMX.
    template = "monitoring/partials/overview_live.html" if request.htmx else "monitoring/index.html"
    return render(request, template, context)


@staff_required
def monitoring_detail(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(MCPServer, pk=pk)
    period = _monitor_range(request)
    limits = traffic.limits_for(server)
    form = MonitorSettingsForm(request.POST or None, instance=limits)

    if request.method == "POST" and form.is_valid():
        changed = form.changed_data
        saved = form.save(commit=False)
        saved.mcp_server, saved.updated_by = server, request.user
        saved.save()
        if changed:
            audit.record(audit.Action.MCP_UPDATED, user=request.user, mcp_server=server, changed_fields=changed, source="monitoring settings")
        messages.success(request, f"Monitoring settings of {server.name} were saved.")
        return redirect("frontend:monitoring-detail", pk=server.pk)

    context = {
        "server": server,
        "row": _monitoring_row(server, period["hours"]),
        "range": period,
        "ranges": MONITOR_RANGES.values(),
        "checks": MCPHealthCheck.objects.filter(mcp_server=server)[:MONITOR_CHECKS_SHOWN],
        "events": MCPMonitorEvent.objects.filter(mcp_server=server)[:MONITOR_EVENTS_SHOWN],
        "hours": _traffic_rows(server, period["hours"]),
        "in_progress": traffic.in_progress(server),
        "form": form,
        "worker_running": monitoring.worker_is_running(),
    }
    template = "monitoring/partials/detail_live.html" if request.htmx else "monitoring/detail.html"
    return render(request, template, context)


def _traffic_rows(server: MCPServer, hours: int = 24) -> list[dict]:
    """
    Gateway traffic over the last `hours`, newest first: one row per hour that had requests,
    or one per day for ranges longer than a day.
    """
    since = timezone.now() - timedelta(hours=hours)
    by_day = hours > 24
    rows_by_time: dict = {}
    for stat in MCPRequestStat.objects.filter(mcp_server=server, bucket_start__gte=since):
        hour = timezone.localtime(stat.bucket_start).replace(minute=0, second=0, microsecond=0)
        if by_day:
            hour = hour.replace(hour=0)
        row = rows_by_time.setdefault(hour, {"hour": hour, "by_day": by_day, "requests": 0, "errors": 0, "denied": 0, "total_ms": 0.0, "max_ms": 0.0})
        row["requests"] += stat.request_count
        row["errors"] += stat.error_count
        row["denied"] += stat.denied_count
        row["total_ms"] += stat.total_response_ms
        row["max_ms"] = max(row["max_ms"], stat.max_response_ms)
    rows = sorted(rows_by_time.values(), key=lambda row: row["hour"], reverse=True)
    for row in rows:
        row["avg_ms"] = round(row["total_ms"] / row["requests"], 1) if row["requests"] else None
    return rows
