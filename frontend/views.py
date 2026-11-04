import logging

from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from backend.services.health import check_platform

from .forms import LoginForm

logger = logging.getLogger(__name__)


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
        "active_user_count": get_user_model().objects.filter(is_active=True).count(),
    }
    return render(request, "dashboard/index.html", context)


@login_required
def system_status(request: HttpRequest) -> HttpResponse:
    """HTMX partial: refreshed periodically on the dashboard."""
    return render(request, "dashboard/partials/system_status.html", {"health": check_platform()})
