import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from backend.models import MCPServer
from backend.permissions import can_manage_mcps
from backend.services import registry
from backend.services.health import check_platform
from backend.validators import slug_format_validator, validate_slug_not_reserved

from .forms import LoginForm, MCPServerForm

logger = logging.getLogger(__name__)

MCP_PAGE_SIZE = 20


def staff_required(view):
    """Logged-in administrators only. Others get a 403 page."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not can_manage_mcps(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


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
    return render(request, "accounts/profile.html")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    context = {
        "counts": registry.get_counts(request.user),
        "active_user_count": get_user_model().objects.filter(is_active=True).count(),
        "recent_servers": registry.list_servers(request.user).order_by("-created_at")[:5],
    }
    return render(request, "dashboard/index.html", context)


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
    servers = registry.list_servers(request.user, **filters)
    page = Paginator(servers, MCP_PAGE_SIZE).get_page(request.GET.get("page"))

    context = {
        "page": page,
        "filters": filters,
        "status_choices": MCPServer.Status.choices,
        "type_choices": MCPServer.DeploymentType.choices,
        "can_manage": can_manage_mcps(request.user),
    }
    # HTMX search/filter/pagination only needs the table, not the whole page.
    template = "mcps/partials/table.html" if request.htmx else "mcps/list.html"
    return render(request, template, context)


@login_required
def mcp_detail(request: HttpRequest, pk: int) -> HttpResponse:
    # Servers the user may not see return 404, so their existence is not revealed.
    server = get_object_or_404(registry.list_servers(request.user), pk=pk)
    context = {
        "server": server,
        "deployments": server.deployments.select_related("requested_by")[:10],
        "can_manage": can_manage_mcps(request.user),
    }
    return render(request, "mcps/detail.html", context)


@staff_required
def mcp_create(request: HttpRequest) -> HttpResponse:
    initial = {}
    if request.GET.get("type") in MCPServer.DeploymentType.values:
        initial["deployment_type"] = request.GET["type"]

    form = MCPServerForm(request.POST or None, initial=initial, slug_check_url=reverse("frontend:mcp-slug-check"))
    if request.method == "POST" and form.is_valid():
        try:
            server = registry.create_server(created_by=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"MCP server “{server.name}” was registered.")
            return redirect("frontend:mcp-detail", pk=server.pk)
    return render(request, "mcps/create.html", {"form": form})


@staff_required
def mcp_edit(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(MCPServer, pk=pk)
    form = MCPServerForm(request.POST or None, instance=server)
    if request.method == "POST" and form.is_valid():
        changes = {name: form.cleaned_data[name] for name in form.changed_data}
        try:
            registry.update_server(server, updated_by=request.user, **changes)
        except (ValidationError, ValueError) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"MCP server “{server.name}” was updated.")
            return redirect("frontend:mcp-detail", pk=server.pk)
    return render(request, "mcps/edit.html", {"form": form, "server": server})


@require_POST
@staff_required
def mcp_delete(request: HttpRequest, pk: int) -> HttpResponse:
    server = get_object_or_404(MCPServer, pk=pk)
    name = server.name
    registry.delete_server(server, deleted_by=request.user)
    messages.success(request, f"MCP server “{name}” was deleted.")
    return redirect("frontend:mcp-list")


@staff_required
def mcp_slug_check(request: HttpRequest) -> HttpResponse:
    """HTMX partial: live feedback on whether a slug can be used."""
    slug = request.GET.get("slug", "").strip()
    error = ""
    if slug:
        try:
            slug_format_validator(slug)
            validate_slug_not_reserved(slug)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            if not registry.is_slug_available(slug):
                error = "This slug is already used by another MCP server."
    return render(request, "mcps/partials/slug_check.html", {"slug": slug, "error": error})
