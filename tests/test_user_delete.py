"""Removing an account for good (accounts.delete_user, the Remove button on a user's page)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import AccessToken, MCPActivityLog, MCPItemAccess, MCPPermission, PermissionLevel
from backend.services import accounts, tokens

from .factories import create_server, create_user, grant

KEY = "introspection-" + "k" * 40


@override_settings(MCP_INTROSPECTION_KEY=KEY)
class RemoveUserTests(TestCase):
    def setUp(self):
        self.root = create_user("root", is_staff=True, is_superuser=True)
        self.server = create_server()
        self.bob = create_user("bob")
        grant(self.bob, self.server, PermissionLevel.CONNECT)
        MCPItemAccess.objects.create(user=self.bob, mcp_server=self.server, item="powerbi", granted_by=self.root)
        _, self.plain = tokens.create_token(owner=self.bob, server=self.server, name="t", expires_in_days=30, created_by=self.bob)
        self.client.force_login(self.root)

    def remove(self, user):
        return self.client.post(reverse("frontend:user-delete", args=[user.pk]))

    def introspect(self):
        return self.client.post("/api/v1/tokens/introspect/", {"token": self.plain, "mcp": self.server.slug},
                                content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {KEY}").json()

    def test_remove_deletes_the_account_and_everything_it_owns(self):
        self.assertTrue(self.introspect()["active"])
        response = self.remove(self.bob)
        self.assertRedirects(response, reverse("frontend:user-list"))
        self.assertFalse(get_user_model().objects.filter(username="bob").exists())
        self.assertFalse(MCPPermission.objects.filter(mcp_server=self.server).exists())
        self.assertFalse(AccessToken.objects.exists())
        self.assertFalse(MCPItemAccess.objects.exists())
        self.assertFalse(self.introspect()["active"])  # the token stops working at once

    def test_history_is_kept_and_the_removal_is_logged(self):
        tokens_before = MCPActivityLog.objects.filter(username="bob").count()
        self.remove(self.bob)
        self.assertEqual(MCPActivityLog.objects.filter(username="bob").count(), tokens_before)
        entry = MCPActivityLog.objects.get(action="user_deleted")
        self.assertEqual((entry.username, entry.metadata["target_user"], entry.metadata["tokens"]), ("root", "bob", 1))
        self.assertContains(self.client.get(reverse("frontend:activity-list")), "removed the user <strong>bob</strong>")

    def test_user_page_shows_remove_with_a_confirmation(self):
        page = self.client.get(reverse("frontend:user-detail", args=[self.bob.pk]))
        self.assertContains(page, reverse("frontend:user-delete", args=[self.bob.pk]))
        self.assertContains(page, 'data-confirm-action="Remove"')

    def test_cannot_remove_yourself(self):
        page = self.client.get(reverse("frontend:user-detail", args=[self.root.pk]))
        self.assertNotContains(page, reverse("frontend:user-delete", args=[self.root.pk]))
        self.remove(self.root)
        self.assertTrue(get_user_model().objects.filter(username="root").exists())

    def test_only_superusers_remove_superusers(self):
        manager = create_user("manager", abilities=["manage_users"])
        other_root = create_user("root2", is_superuser=True)
        self.client.force_login(manager)
        self.remove(other_root)
        self.assertTrue(get_user_model().objects.filter(username="root2").exists())
        self.remove(self.bob)  # a normal user: allowed
        self.assertFalse(get_user_model().objects.filter(username="bob").exists())

    def test_people_who_cannot_manage_users_cannot_remove(self):
        self.client.force_login(create_user("carol"))
        self.assertEqual(self.remove(self.bob).status_code, 403)
        self.assertTrue(get_user_model().objects.filter(username="bob").exists())

    def test_removal_needs_post(self):
        self.assertEqual(self.client.get(reverse("frontend:user-delete", args=[self.bob.pk])).status_code, 405)

    def test_service_accounts_can_be_removed(self):
        account = accounts.create_service_account(name="ppc-server", description="", created_by=self.root)
        self.remove(account.user)
        self.assertFalse(get_user_model().objects.filter(username="svc-ppc-server").exists())
        self.assertEqual(MCPActivityLog.objects.get(action="user_deleted").metadata["kind"], "service account")
