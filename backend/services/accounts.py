"""User administration: listing users, activation and service accounts."""

import re

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q

from backend.models import ServiceAccount

from . import audit

SERVICE_ACCOUNT_PREFIX = "svc-"
_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")


def list_users(*, search: str = "", kind: str = ""):
    """All users with their service-account details and permission counts. kind: "people" or "service"."""
    users = (
        get_user_model()
        .objects.select_related("service_account")
        .annotate(permission_count=Count("mcp_permissions", distinct=True))
        .order_by("username")
    )
    search = search.strip()
    if search:
        users = users.filter(
            Q(username__icontains=search) | Q(first_name__icontains=search)
            | Q(last_name__icontains=search) | Q(email__icontains=search)
        )
    if kind == "service":
        users = users.filter(service_account__isnull=False)
    elif kind == "people":
        users = users.filter(service_account__isnull=True)
    return users


@transaction.atomic
def create_service_account(*, name: str, description: str, created_by) -> ServiceAccount:
    """Create a service account named "svc-<name>". It has no password and cannot log in."""
    name = name.strip().lower()
    if not _SERVICE_NAME.match(name):
        raise ValidationError(
            {"name": "Use 3–40 lowercase letters, numbers and hyphens, starting with a letter (e.g. ppc-server)."}
        )
    username = SERVICE_ACCOUNT_PREFIX + name
    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        raise ValidationError({"name": f"An account named {username} already exists."})

    user = User(username=username, is_staff=False, is_superuser=False)
    user.set_unusable_password()
    user.save()
    account = ServiceAccount.objects.create(user=user, description=description.strip(), created_by=created_by)
    audit.record(audit.Action.SERVICE_ACCOUNT_CREATED, user=created_by, target_user=username)
    return account


@transaction.atomic
def create_user(*, username: str, password: str, first_name: str = "", last_name: str = "", email: str = "", created_by):
    """A normal person's account. It starts with no abilities and no MCP permissions."""
    User = get_user_model()
    username = username.strip()
    if User.objects.filter(username__iexact=username).exists():
        raise ValidationError({"username": f"An account named {username} already exists."})
    if username.lower().startswith(SERVICE_ACCOUNT_PREFIX):
        raise ValidationError({"username": f"Names starting with {SERVICE_ACCOUNT_PREFIX} are for service accounts."})
    user = User(username=username, first_name=first_name.strip(), last_name=last_name.strip(),
                email=email.strip(), is_staff=False, is_superuser=False)
    user.set_password(password)
    user.save()
    audit.record(audit.Action.USER_CREATED, user=created_by, target_user=username)
    return user


@transaction.atomic
def set_capability(user, code: str, enabled: bool, *, changed_by) -> None:
    """Switch one platform ability on or off for a user (superusers only)."""
    from django.contrib.auth.models import Permission

    from backend.permissions import CAPABILITY_CODES

    if not (changed_by.is_active and changed_by.is_superuser):
        raise PermissionDenied("Only superusers can change abilities.")
    if code not in CAPABILITY_CODES:
        raise ValidationError("Unknown ability.")
    if user.is_superuser:
        raise ValidationError("Superusers always have every ability.")
    if hasattr(user, "service_account"):
        raise ValidationError("Service accounts cannot have platform abilities.")
    permission = Permission.objects.get(content_type__app_label="backend", codename=code)
    if enabled:
        user.user_permissions.add(permission)
    else:
        user.user_permissions.remove(permission)
    audit.record(audit.Action.CAPABILITY_CHANGED, user=changed_by, target_user=user.get_username(),
                 capability=code, enabled=enabled)


@transaction.atomic
def set_active(user, active: bool, *, changed_by) -> None:
    """
    Activate or deactivate an account. A deactivated account cannot log in,
    its open sessions stop working, and its tokens are refused.
    """
    if user.pk == changed_by.pk and not active:
        raise ValidationError("You cannot deactivate your own account.")
    if user.is_superuser and not changed_by.is_superuser:
        raise ValidationError("Only a superuser can turn a superuser's account on or off.")
    if user.is_active == active:
        return
    user.is_active = active
    user.save(update_fields=["is_active"])
    audit.record(
        audit.Action.USER_ACTIVATED if active else audit.Action.USER_DEACTIVATED,
        user=changed_by,
        target_user=user.get_username(),
    )
