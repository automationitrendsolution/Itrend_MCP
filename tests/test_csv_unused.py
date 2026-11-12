from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from backend.models import AccessToken, MCPActivityLog
from backend.services import access, audit, tokens

from .factories import create_server, create_user


class AuditCsvTests(TestCase):
    def setUp(self):
        self.root = create_user("root", is_staff=True, is_superuser=True)
        audit.record(audit.Action.LOGIN, user=self.root)
        audit.record(audit.Action.LOGIN_FAILED, username="=cmd|evil", result=MCPActivityLog.Result.FAILURE)

    def _csv(self, query=""):
        response = self.client.get(reverse("frontend:activity-export") + query)
        self.assertEqual(response.status_code, 200)
        return b"".join(response.streaming_content).decode("utf-8-sig")

    def test_export_follows_filters_and_blocks_formulas(self):
        self.client.force_login(self.root)
        everything = self._csv()
        self.assertIn("Time (UTC),Person,Action", everything)
        self.assertIn("'=cmd|evil", everything)
        problems = self._csv("?problems=1")
        self.assertIn("Failed login", problems)
        self.assertNotIn(",root,Login,", problems)

    def test_export_needs_audit_ability(self):
        self.client.force_login(create_user("bob"))
        self.assertEqual(self.client.get(reverse("frontend:activity-export")).status_code, 403)


class UnusedTokenTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.server = create_server()
        root = create_user("root", is_staff=True, is_superuser=True)
        access.grant_permission(server=self.server, user=self.alice, level="connect", granted_by=root)
        self.old, _ = tokens.create_token(owner=self.alice, server=self.server, name="old", expires_in_days=90, created_by=self.alice)
        self.fresh, _ = tokens.create_token(owner=self.alice, server=self.server, name="fresh", expires_in_days=90, created_by=self.alice)
        long_ago = timezone.now() - timedelta(days=45)
        AccessToken.objects.filter(pk__in=[self.old.pk, self.fresh.pk]).update(created_at=long_ago)
        AccessToken.objects.filter(pk=self.fresh.pk).update(last_used_at=timezone.now())

    def test_only_idle_tokens_are_unused(self):
        self.assertEqual(list(AccessToken.objects.unused()), [AccessToken.objects.get(pk=self.old.pk)])
        self.assertTrue(AccessToken.objects.get(pk=self.old.pk).is_unused)
        self.assertFalse(AccessToken.objects.get(pk=self.fresh.pk).is_unused)

    def test_pages_warn_and_one_click_revokes(self):
        self.client.force_login(self.alice)
        self.assertContains(self.client.get(reverse("frontend:token-list")), "Revoke unused")
        self.assertContains(self.client.get(reverse("frontend:dashboard")), "not been used for 30+ days")
        self.client.post(reverse("frontend:token-revoke-unused"))
        self.assertIsNotNone(AccessToken.objects.get(pk=self.old.pk).revoked_at)
        self.assertIsNone(AccessToken.objects.get(pk=self.fresh.pk).revoked_at)

    def test_revoke_unused_only_touches_your_own(self):
        self.client.force_login(create_user("bob"))
        self.client.post(reverse("frontend:token-revoke-unused"))
        self.assertIsNone(AccessToken.objects.get(pk=self.old.pk).revoked_at)
