from django.apps import AppConfig


class BackendConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend"

    def ready(self) -> None:
        from . import signals  # noqa: F401  (connects the activity-log receivers)
