"""Stage 6 pages: Audit Logs period filter, a user's access history, the Profile page."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from backend.models import MCPActivityLog, PermissionLevel
from backend.services import access, audit

from .factories import create_user, grant, restricted_server


class AuditPeriodTests(TestCase):
    def setUp(self):
        self.client.force_login(create_user("boss", is_staff=True))
        audit.record(audit.Action.LOGIN, username="recent")
        old = audit.record(audit.Action.LOGIN, username="old")
        MCPActivityLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=10))

    def names(self, **query):
        response = self.client.get(reverse("frontend:activity-list"), query)
        # Signing the test administrator in is logged too; only the two prepared entries matter here.
        return [entry.username for entry in response.context["page"].object_list if entry.username != "boss"]

    def test_period_filter(self):
        self.assertEqual(self.names(period="7d"), ["recent"])
        self.assertEqual(self.names(period="30d"), ["recent", "old"])
        self.assertEqual(self.names(), ["recent", "old"])

    def test_unknown_period_means_any_time(self):
        self.assertEqual(self.names(period="forever"), ["recent", "old"])


class UserAccessHistoryTests(TestCase):
    def test_shows_what_the_account_did_and_what_was_done_to_it(self):
        boss = create_user("boss", is_staff=True)
        alice = create_user()
        server = restricted_server()
        access.grant_permission(server=server, user=alice, level=PermissionLevel.CONNECT, granted_by=boss)
        audit.record(audit.Action.LOGIN, user=alice)
        audit.record(audit.Action.LOGIN, user=boss)  # someone else's sign-in: not alice's history

        self.client.force_login(boss)
        response = self.client.get(reverse("frontend:user-detail", args=[alice.pk]))
        actions = [entry.action for entry in response.context["activity"]]
        self.assertEqual(sorted(actions), ["login", "permission_granted"])
        self.assertContains(response, "Access history")


class ProfileTests(TestCase):
    def test_lists_your_own_permissions(self):
        alice = create_user()
        grant(alice, restricted_server("Secret MCP", "secret"), PermissionLevel.VIEW)
        self.client.force_login(alice)
        response = self.client.get(reverse("frontend:profile"))
        self.assertContains(response, "Secret MCP")
        self.assertContains(response, 'data-pg-theme="dark"')  # the Appearance setting
        self.assertNotContains(response, "Administration")
