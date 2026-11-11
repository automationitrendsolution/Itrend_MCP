"""
Platform abilities replaced the single "staff = administrator" switch. So that nobody loses
access, every existing staff member (who is not a superuser) gets all five abilities.
"""

from django.conf import settings
from django.db import migrations

ABILITIES = [
    ("manage_mcps", "Run MCP servers"),
    ("manage_access", "Manage MCP access"),
    ("view_monitoring", "See monitoring"),
    ("manage_users", "Manage users"),
    ("view_audit_log", "See the audit log"),
]


def grant(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    content_type, _ = ContentType.objects.get_or_create(app_label="backend", model="platformcapability")
    permissions = [
        Permission.objects.get_or_create(content_type=content_type, codename=code, defaults={"name": name})[0]
        for code, name in ABILITIES
    ]
    for user in User.objects.filter(is_staff=True, is_superuser=False):
        user.user_permissions.add(*permissions)


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0005_tokens_lifetime_and_abilities"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
