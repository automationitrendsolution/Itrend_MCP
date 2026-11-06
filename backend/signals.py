"""Write logins, logouts and failed logins to the activity log."""

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import audit


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    audit.record(audit.Action.LOGIN, user=user)


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    if user is not None:
        audit.record(audit.Action.LOGOUT, user=user)


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request=None, **kwargs):
    # Django removes the password from `credentials` before sending this signal.
    audit.record(audit.Action.LOGIN_FAILED, username=str(credentials.get("username", "")), result=audit.Result.FAILURE)
