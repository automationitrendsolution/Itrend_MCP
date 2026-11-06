"""Validation rules for the MCP registry, shared by website forms and the API."""

from collections.abc import Iterable

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator

# Slugs become public URLs (https://mcp.itrendsolution.com/<slug>/), so they must
# not collide with the website's own top-level paths.
RESERVED_SLUGS = frozenset(
    {
        "accounts", "activity", "admin", "api", "dashboard", "health", "login", "logout",
        "mcp", "mcps", "media", "permissions", "static", "tokens", "users", "www",
    }
)

# Configuration keys that look like credentials. Secrets must never be stored
# in the registry; they will be handled by the deployment secret store (Phase 5).
SECRET_KEY_MARKERS = ("password", "passwd", "secret", "token", "apikey", "api_key", "private_key", "credential")

slug_format_validator = RegexValidator(
    regex=r"^[a-z][a-z0-9-]{1,48}[a-z0-9]$",
    message="Use 3–50 lowercase letters, numbers and hyphens, starting with a letter and not ending with a hyphen.",
)

http_url_validator = URLValidator(schemes=["http", "https"])


def validate_slug_not_reserved(value: str) -> None:
    if value in RESERVED_SLUGS:
        raise ValidationError("“%(value)s” is reserved by the platform. Choose another slug.", params={"value": value})


def _iter_keys(data) -> Iterable[str]:
    if isinstance(data, dict):
        for key, value in data.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_keys(item)


def validate_configuration(value) -> None:
    if not isinstance(value, dict):
        raise ValidationError("Configuration must be a JSON object, for example {\"max_rows\": 500}.")
    for key in _iter_keys(value):
        normalised = key.lower().replace("-", "_")
        if any(marker in normalised for marker in SECRET_KEY_MARKERS):
            raise ValidationError(
                "“%(key)s” looks like a secret. Passwords, tokens and keys must not be stored in the registry.",
                params={"key": key},
            )
