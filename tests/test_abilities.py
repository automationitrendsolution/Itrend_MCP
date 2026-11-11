"""Per-user platform abilities, Add user, lifetime tokens and deleting tokens."""

from django.test import TestCase
from django.urls import reverse

from backend.models import AccessToken
from backend.permissions import has_capability
from backend.services import tokens

from .factories import create_server, create_user


class AbilityTests(TestCase):
    def setUp(self):
        self.root = create_user("root", is_staff=True, is_superuser=True)
        self.bob = create_user("bob")

    def toggle(self, code, on):
        return self.client.post(reverse("frontend:user-capability", args=[self.bob.pk, code]), {"on": "1" if on else "0"})

    def test_superuser_switches_an_ability_on_and_off(self):
        self.client.force_login(self.root)
        self.toggle("manage_users", True)
        self.bob.refresh_from_db()
        self.assertTrue(has_capability(type(self.bob).objects.get(pk=self.bob.pk), "manage_users"))
        self.toggle("manage_users", False)
        self.assertFalse(has_capability(type(self.bob).objects.get(pk=self.bob.pk), "manage_users"))

    def test_only_superusers_can_switch_abilities(self):
        self.client.force_login(create_user("helper", abilities=["manage_users"]))
        self.assertEqual(self.toggle("manage_users", True).status_code, 403)

    def test_each_ability_opens_only_its_own_pages(self):
        auditor = create_user("auditor", abilities=["view_audit_log"])
        self.client.force_login(auditor)
        self.assertEqual(self.client.get(reverse("frontend:activity-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("frontend:user-list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("frontend:monitoring")).status_code, 403)

    def test_user_with_manage_users_can_add_a_user(self):
        self.client.force_login(create_user("helper", abilities=["manage_users"]))
        response = self.client.post(reverse("frontend:user-create"), {
            "username": "carol", "first_name": "Carol", "last_name": "", "email": "",
            "password1": "a-Long-unusual-pw-42", "password2": "a-Long-unusual-pw-42",
        })
        carol = type(self.bob).objects.get(username="carol")
        self.assertRedirects(response, reverse("frontend:user-detail", args=[carol.pk]))
        self.assertFalse(carol.is_staff)
        self.assertFalse(carol.user_permissions.exists())


class TokenLifetimeAndDeleteTests(TestCase):
    def setUp(self):
        self.user = create_user("alice")
        self.server = create_server()

    def test_lifetime_token_never_expires(self):
        token, plain = tokens.create_token(owner=self.user, server=self.server, name="forever",
                                           expires_in_days=tokens.NEVER_EXPIRES, created_by=self.user)
        self.assertIsNone(token.expires_at)
        self.assertEqual(token.state, AccessToken.State.ACTIVE)
        self.assertTrue(tokens.introspect(plain, self.server.slug).active)

    def test_owner_can_delete_a_token_and_it_stops_working(self):
        token, plain = tokens.create_token(owner=self.user, server=self.server, name="laptop",
                                           expires_in_days=30, created_by=self.user)
        self.client.force_login(self.user)
        self.client.post(reverse("frontend:token-delete", args=[token.pk]))
        self.assertFalse(AccessToken.objects.filter(pk=token.pk).exists())
        self.assertFalse(tokens.introspect(plain, self.server.slug).active)

    def test_others_cannot_delete_someone_elses_token(self):
        token, _ = tokens.create_token(owner=self.user, server=self.server, name="laptop",
                                       expires_in_days=30, created_by=self.user)
        self.client.force_login(create_user("mallory"))
        self.assertEqual(self.client.post(reverse("frontend:token-delete", args=[token.pk])).status_code, 403)
        self.assertTrue(AccessToken.objects.filter(pk=token.pk).exists())


class TesterFindingsTests(TestCase):
    """Holes found by walking through the site as each kind of user."""

    def setUp(self):
        from backend.models import PermissionLevel

        from .factories import grant, restricted_server

        self.level = PermissionLevel
        self.grant = grant
        self.root = create_user("root", is_staff=True, is_superuser=True)
        self.secret = restricted_server()

    def test_manage_users_cannot_switch_off_a_superuser(self):
        self.client.force_login(create_user("helper", abilities=["manage_users"]))
        self.client.post(reverse("frontend:user-set-active", args=[self.root.pk]), {"active": "0"})
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_active)

    def test_service_account_token_needs_the_creator_to_have_connect(self):
        from .factories import create_service_account

        helper = create_user("helper", abilities=["manage_users"])
        service = create_service_account()
        self.grant(service, self.secret, self.level.CONNECT)
        self.client.force_login(helper)
        response = self.client.post(reverse("frontend:user-token-create", args=[service.pk]),
                                    {"name": "x", "mcp_server": self.secret.pk, "expires_in_days": tokens.NEVER_EXPIRES})
        self.assertNotContains(response, "itmcp_")
        self.assertFalse(AccessToken.objects.filter(user=service).exists())

    def test_managing_access_does_not_give_data_access(self):
        from backend.services import access

        manager = create_user("manager", abilities=["manage_access"])
        self.assertTrue(access.can_admin(manager, self.secret))
        self.assertFalse(access.can_connect(manager, self.secret))
        self.grant(manager, self.secret, self.level.CONNECT)
        self.assertTrue(access.can_connect(manager, self.secret))

    def test_see_monitoring_is_read_only_but_sees_every_mcp(self):
        watcher = create_user("watcher", abilities=["view_monitoring"])
        self.client.force_login(watcher)
        self.assertEqual(self.client.post(reverse("frontend:monitoring-detail", args=[self.secret.pk]), {}).status_code, 403)
        self.assertEqual(self.client.get(reverse("frontend:mcp-detail", args=[self.secret.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("frontend:mcp-logs", args=[self.secret.pk])).status_code, 200)

    def test_manage_users_cannot_revoke_a_superusers_token(self):
        token, _ = tokens.create_token(owner=self.root, server=self.secret, name="root",
                                       expires_in_days=30, created_by=self.root)
        self.client.force_login(create_user("helper", abilities=["manage_users"]))
        self.assertEqual(self.client.post(reverse("frontend:token-revoke", args=[token.pk])).status_code, 403)

    def test_unknown_ability_is_not_found(self):
        self.client.force_login(self.root)
        bob = create_user("bob")
        response = self.client.post(reverse("frontend:user-capability", args=[bob.pk, "nope"]), {"on": "1"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 404)

    def test_overview_counts_every_mcp(self):
        for i in range(10):
            create_server(f"Bulk {i}", f"bulk-{i}")
        self.client.force_login(self.root)
        response = self.client.get(reverse("frontend:dashboard"))
        self.assertEqual(response.context["kpi"]["total"], 11)
        self.assertEqual(len(response.context["lines"]), 8)

    def test_role_label_follows_abilities(self):
        from frontend.templatetags.mcp_tags import role_label

        self.assertEqual(role_label(create_user("aud", abilities=["view_audit_log"])), "Administrator")
        self.assertEqual(role_label(create_user("old", is_staff=True, abilities=[])), "Member")
        self.assertEqual(role_label(self.root), "Superuser")
