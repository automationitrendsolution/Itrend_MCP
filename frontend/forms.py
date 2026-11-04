from django import forms
from django.contrib.auth.forms import AuthenticationForm

from backend.services import login_protection

LOCKED_OUT_MESSAGE = "Too many failed login attempts. Please wait a few minutes and try again."


class LoginForm(AuthenticationForm):
    """Django's login form with Bootstrap styling and failed-login lockout."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "locked_out": LOCKED_OUT_MESSAGE,
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"class": "form-control", "placeholder": "Username", "autocomplete": "username"}
        )
        self.fields["password"].widget.attrs.update(
            {"class": "form-control", "placeholder": "Password", "autocomplete": "current-password"}
        )

    def clean(self):
        username = self.data.get("username", "")
        ip = login_protection.get_client_ip(self.request)

        if login_protection.is_locked_out(username, ip):
            raise forms.ValidationError(self.error_messages["locked_out"], code="locked_out")

        try:
            cleaned_data = super().clean()
        except forms.ValidationError:
            login_protection.record_failed_login(username, ip)
            raise

        login_protection.reset_failed_logins(username, ip)
        return cleaned_data
