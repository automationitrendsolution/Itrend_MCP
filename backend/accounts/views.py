import logging

from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .forms import LoginForm

logger = logging.getLogger(__name__)


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
