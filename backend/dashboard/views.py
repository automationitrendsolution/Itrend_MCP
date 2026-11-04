from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from backend.api.health import check_platform


@login_required
def index(request: HttpRequest) -> HttpResponse:
    context = {
        "active_user_count": get_user_model().objects.filter(is_active=True).count(),
    }
    return render(request, "dashboard/index.html", context)


@login_required
def system_status(request: HttpRequest) -> HttpResponse:
    """HTMX partial: refreshed periodically on the dashboard."""
    return render(request, "dashboard/partials/system_status.html", {"health": check_platform()})
