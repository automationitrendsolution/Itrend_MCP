from django import forms
from django.contrib.auth.forms import AuthenticationForm

from backend.models import PERMISSION_LEVEL_HELP, MCPServer, PermissionLevel
from backend.services import access, audit, login_protection
from backend.services.tokens import DEFAULT_EXPIRY_DAYS, EXPIRY_CHOICES_DAYS


def apply_bootstrap(form: forms.BaseForm) -> None:
    """Give every widget the matching Bootstrap class."""
    for field in form.fields.values():
        if isinstance(field.widget, (forms.RadioSelect, forms.CheckboxInput)):
            continue
        css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
        existing = field.widget.attrs.get("class", "")
        if css not in existing:
            field.widget.attrs["class"] = f"{css} {existing}".strip()


def mark_invalid(form: forms.BaseForm) -> None:
    """Mark invalid inputs so Bootstrap shows them in red."""
    for name in form.errors:
        if name in form.fields:
            widget = form.fields[name].widget
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()

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
            audit.record(
                audit.Action.LOGIN_FAILED, username=username, result=audit.Result.DENIED, reason="locked_out"
            )
            raise forms.ValidationError(self.error_messages["locked_out"], code="locked_out")

        try:
            cleaned_data = super().clean()
        except forms.ValidationError:
            login_protection.record_failed_login(username, ip)
            raise

        login_protection.reset_failed_logins(username, ip)
        return cleaned_data


class BootstrapForm(forms.Form):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        apply_bootstrap(self)

    def full_clean(self):
        super().full_clean()
        mark_invalid(self)


class AccessTokenForm(BootstrapForm):
    """Generate a token for one MCP server."""

    mcp_server = forms.ModelChoiceField(queryset=MCPServer.objects.none(), label="MCP server", empty_label=None)
    name = forms.CharField(
        max_length=100,
        help_text="Where will you use it? For example “Claude Desktop – laptop” or “PPC production”.",
    )
    expires_in_days = forms.TypedChoiceField(
        label="Expires after",
        coerce=int,
        choices=[(days, f"{days} days") for days in EXPIRY_CHOICES_DAYS],
        initial=DEFAULT_EXPIRY_DAYS,
        help_text="Tokens always expire. Create a new one before it does.",
    )

    def __init__(self, *args, servers=(), **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["mcp_server"].queryset = MCPServer.objects.filter(pk__in=[s.pk for s in servers])


class PermissionGrantForm(BootstrapForm):
    """Give a user a permission level on an MCP server. Either field can be fixed by the page."""

    user = forms.ModelChoiceField(queryset=access.grantable_users(), label="User or service account")
    mcp_server = forms.ModelChoiceField(queryset=MCPServer.objects.all(), label="MCP server")
    permission_level = forms.ChoiceField(
        label="Permission",
        choices=[(level.value, f"{level.label} – {PERMISSION_LEVEL_HELP[level]}") for level in PermissionLevel],
        initial=PermissionLevel.CONNECT,
    )

    def __init__(self, *args, server=None, user=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if server is not None:
            self.fields["mcp_server"].queryset = MCPServer.objects.filter(pk=server.pk)
            self.fields["mcp_server"].initial = server.pk
            self.fields["mcp_server"].widget = forms.HiddenInput()
        if user is not None:
            self.fields["user"].queryset = access.grantable_users().filter(pk=user.pk)
            self.fields["user"].initial = user.pk
            self.fields["user"].widget = forms.HiddenInput()


class ServiceAccountForm(BootstrapForm):
    name = forms.CharField(
        max_length=40,
        help_text="Lowercase letters, numbers and hyphens. The account will be called svc-<name>, e.g. svc-ppc-server.",
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Which system uses this account and who is responsible for it.",
    )
