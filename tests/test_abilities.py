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
