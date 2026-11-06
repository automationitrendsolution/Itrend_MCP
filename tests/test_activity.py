"""The activity log records the events the brief lists."""

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import MCPActivityLog
from backend.services import registry

from .factories import PASSWORD, create_server, create_user


def actions():
    return list(MCPActivityLog.objects.order_by("id").values_list("action", "result", "username"))


class LoginActivityTests(TestCase):
    def setUp(self):
        cache.clear()
        create_user()

    def login(self, password):
        return self.client.post(reverse("frontend:login"), {"username": "alice", "password": password}, REMOTE_ADDR="10.1.2.3")

    def test_login_logout_and_failure(self):
        self.login("wrong")
        self.login(PASSWORD)
        self.client.post(reverse("frontend:logout"), REMOTE_ADDR="10.1.2.3")
        self.assertEqual(actions(), [
            ("login_failed", "failure", "alice"), ("login", "success", "alice"), ("logout", "success", "alice"),
        ])
        self.assertEqual(set(MCPActivityLog.objects.values_list("source_ip", flat=True)), {"10.1.2.3"})

    def test_password_is_never_logged(self):
        self.login("Sup3r-Secret-Guess")
        self.assertNotIn("Sup3r-Secret-Guess", str(list(MCPActivityLog.objects.values())))

    @override_settings(LOGIN_MAX_FAILED_ATTEMPTS=2)
    def test_locked_out_attempts_are_logged_as_denied(self):
        for _ in range(3):
            self.login("wrong")
        self.assertEqual(actions()[-1], ("login_failed", "denied", "alice"))
        self.assertEqual(MCPActivityLog.objects.order_by("id").last().metadata, {"reason": "locked_out"})


class RegistryActivityTests(TestCase):
    def test_create_update_delete_are_logged(self):
        admin = create_user("boss", is_staff=True)
        server = registry.create_server(created_by=admin, name="A", slug="aaa", deployment_type="zip")
        registry.update_server(server, updated_by=admin, description="changed", name="A")
        registry.delete_server(server, deleted_by=admin)
        self.assertEqual([a for a, _, _ in actions()], ["mcp_created", "mcp_updated", "mcp_deleted"])
        updated = MCPActivityLog.objects.get(action="mcp_updated")
        self.assertEqual(updated.metadata["changed_fields"], ["description"])
        deleted = MCPActivityLog.objects.get(action="mcp_deleted")
        # The MCP is gone, but the log entry keeps its slug.
        self.assertEqual((deleted.mcp_server, deleted.mcp_slug), (None, "aaa"))


class ActivityLogIsReadOnlyInAdminTests(TestCase):
    def test_admin_cannot_change_entries(self):
        admin = create_user("boss", is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        entry = MCPActivityLog.objects.create(action="login", username="x")
        self.assertEqual(self.client.get(reverse("admin:backend_mcpactivitylog_changelist")).status_code, 200)
        self.assertEqual(self.client.post(reverse("admin:backend_mcpactivitylog_delete", args=[entry.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:backend_mcpactivitylog_add")).status_code, 403)

    def test_deleting_a_user_keeps_their_log_entries(self):
        alice = create_user()
        create_server()
        MCPActivityLog.objects.create(action="login", user=alice, username="alice")
        alice.delete()
        self.assertEqual(MCPActivityLog.objects.get().username, "alice")
