from django import forms
from django.contrib.auth.forms import AuthenticationForm

from backend.models import MCPServer
from backend.services import login_protection
from backend.services.registry import IMMUTABLE_FIELDS

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


class MCPServerForm(forms.ModelForm):
    """Register or edit an MCP server. Rules are enforced by the model and backend.services.registry."""

    class Meta:
        model = MCPServer
        fields = (
            "deployment_type", "name", "slug", "description", "transport_type",
            "endpoint", "internal_endpoint", "access_policy", "configuration",
            "status", "health_status",
        )
        widgets = {
            "deployment_type": forms.RadioSelect,
            "description": forms.Textarea(attrs={"rows": 3}),
            "configuration": forms.Textarea(attrs={"rows": 4, "class": "font-monospace"}),
        }
        labels = {"endpoint": "Endpoint URL", "health_status": "Health"}

    def __init__(self, *args, slug_check_url: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        is_edit = self.instance.pk is not None

        if is_edit:
            # Changing these would break the connection URL or the deployment record.
            for name in IMMUTABLE_FIELDS:
                self.fields[name].disabled = True
            self.fields["status"].help_text = "Set by hand until automated deployments arrive (Phase 5)."
            self.fields["health_status"].help_text = "Set by hand until automated health checks arrive (Phase 6)."
        else:
            del self.fields["status"]
            del self.fields["health_status"]
            if slug_check_url:
                # HTMX: check slug availability while typing.
                self.fields["slug"].widget.attrs.update(
                    {
                        "hx-get": slug_check_url,
                        "hx-trigger": "input changed delay:400ms",
                        "hx-target": "#slugFeedback",
                        "autocomplete": "off",
                    }
                )

        self.fields["configuration"].required = False
        self.fields["endpoint"].widget.attrs["placeholder"] = "https://mcp.example.com/mcp"
        self.fields["internal_endpoint"].widget.attrs["placeholder"] = "http://mcp-mysql:8000/mcp"

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.RadioSelect):
                continue
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} {existing}".strip()

    def clean_configuration(self):
        return self.cleaned_data.get("configuration") or {}

    def full_clean(self):
        super().full_clean()
        # Mark invalid inputs so Bootstrap shows them in red.
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()
