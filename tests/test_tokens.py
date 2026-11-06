"""Access tokens: generation, storage, revocation and checking (introspection)."""

import hashlib
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from backend.models import AccessToken, MCPActivityLog, MCPPermission, MCPServer, PermissionLevel
from backend.services import tokens

from .factories import create_server, create_service_account, create_token, create_user, grant, restricted_server


class CreateTokenTests(TestCase):
    def setUp(self):
        self.alice = create_user()
        self.server = create_server()

    def test_token_format_and_only_hash_is_stored(self):
        token, plaintext = create_token(self.alice, self.server)
        self.assertTrue(plaintext.startswith("itmcp_"))
        self.assertGreaterEqual(len(plaintext), 6 + 43)
        self.assertEqual(token.token_hash, hashlib.sha256(plaintext.encode()).hexdigest())
        self.assertEqual(token.token_prefix, plaintext[:14])
        stored = AccessToken.objects.filter(pk=token.pk).values()[0]
        self.assertNotIn(plaintext, [str(value) for value in stored.values()])

    def test_tokens_are_unique(self):
        values = {create_token(self.alice, self.server, name=f"t{i}")[1] for i in range(5)}
        self.assertEqual(len(values), 5)

    def test_expiry(self):
        token, _ = create_token(self.alice, self.server, days=7)
        self.assertAlmostEqual(token.expires_at, timezone.now() + timedelta(days=7), delta=timedelta(seconds=10))
        with self.assertRaises(ValidationError):
            create_token(self.alice, self.server, days=10_000)

    def test_creation_is_logged_without_the_secret(self):
        token, plaintext = create_token(self.alice, self.server)
        entry = MCPActivityLog.objects.get(action="token_created")
        self.assertEqual((entry.username, entry.mcp_slug, entry.metadata["token_id"]), ("alice", "mysql", token.pk))
        self.assertNotIn(plaintext, str(entry.metadata))

    def test_needs_connect_permission(self):
        secret = restricted_server()
        with self.assertRaises(ValidationError):
            create_token(self.alice, secret)
        grant(self.alice, secret, PermissionLevel.VIEW)
        with self.assertRaises(ValidationError):
            create_token(self.alice, secret)
        MCPPermission.objects.update(permission_level=PermissionLevel.CONNECT)
        create_token(self.alice, secret)

    def test_external_mcps_cannot_get_tokens(self):
        external = create_server("Ext", "ext", deployment_type=MCPServer.DeploymentType.EXTERNAL, endpoint="https://x.example.com/mcp")
        with self.assertRaises(ValidationError):
            create_token(self.alice, external)

    def test_who_may_create_for_whom(self):
        admin, bob = create_user("boss", is_staff=True), create_user("bob")
        service = create_service_account()
        grant(service, self.server, PermissionLevel.CONNECT)
        with self.assertRaises(PermissionDenied):  # a person for another person
            create_token(bob, self.server, created_by=self.alice)
        with self.assertRaises(PermissionDenied):  # even administrators cannot create personal tokens for others
            create_token(bob, self.server, created_by=admin)
        with self.assertRaises(PermissionDenied):  # non-admins cannot create service tokens
            create_token(service, self.server, created_by=self.alice)
        token, _ = create_token(service, self.server, created_by=admin)
        self.assertEqual((token.user, token.created_by), (service, admin))


class RevokeTokenTests(TestCase):
    def setUp(self):
        self.alice = create_user()
        self.token, _ = create_token(self.alice, create_server())

    def test_owner_can_revoke(self):
        tokens.revoke_token(self.token, revoked_by=self.alice)
        self.token.refresh_from_db()
        self.assertEqual(self.token.state, AccessToken.State.REVOKED)
        self.assertEqual(self.token.revoked_by, self.alice)
        self.assertTrue(MCPActivityLog.objects.filter(action="token_revoked").exists())

    def test_others_cannot_but_admins_can(self):
        with self.assertRaises(PermissionDenied):
            tokens.revoke_token(self.token, revoked_by=create_user("bob"))
        tokens.revoke_token(self.token, revoked_by=create_user("boss", is_staff=True))
        self.assertIsNotNone(AccessToken.objects.get().revoked_at)

    def test_revoking_twice_is_harmless(self):
        tokens.revoke_token(self.token, revoked_by=self.alice)
        tokens.revoke_token(self.token, revoked_by=self.alice)
        self.assertEqual(MCPActivityLog.objects.filter(action="token_revoked").count(), 1)


class IntrospectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = create_user()
        self.server = create_server()
        self.token, self.plaintext = create_token(self.alice, self.server)

    def check(self, token=None, slug="mysql"):
        return tokens.introspect(token or self.plaintext, slug)

    def test_valid_token(self):
        result = self.check()
        self.assertTrue(result.active)
        self.assertEqual((result.token, result.level), (self.token, PermissionLevel.CONNECT))
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)
        self.assertEqual(MCPActivityLog.objects.filter(action="mcp_connection", result="success").count(), 1)

    def test_connections_are_logged_at_most_every_few_minutes(self):
        for _ in range(5):
            self.check()
        self.assertEqual(MCPActivityLog.objects.filter(action="mcp_connection").count(), 1)

    def test_unknown_and_malformed_tokens(self):
        self.assertEqual(self.check("itmcp_" + "x" * 43).reason, "unknown_token")
        self.assertEqual(self.check("not-a-platform-token").reason, "unknown_token")

    def test_token_for_another_mcp_is_refused(self):
        create_server("Other", "other")
        self.assertEqual(self.check(slug="other").reason, "wrong_mcp")

    def test_revoked(self):
        tokens.revoke_token(self.token, revoked_by=self.alice)
        self.assertEqual(self.check().reason, "revoked")

    def test_expired(self):
        AccessToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.check().reason, "expired")

    def test_inactive_owner(self):
        self.alice.is_active = False
        self.alice.save()
        self.assertEqual(self.check().reason, "user_inactive")

    def test_losing_permission_stops_the_token_immediately(self):
        self.server.access_policy = MCPServer.AccessPolicy.RESTRICTED
        self.server.save()
        self.assertEqual(self.check().reason, "no_permission")
        grant(self.alice, self.server, PermissionLevel.CONNECT)
        self.assertTrue(self.check().active)

    def test_denials_are_logged(self):
        tokens.revoke_token(self.token, revoked_by=self.alice)
        self.check()
        entry = MCPActivityLog.objects.get(action="mcp_connection")
        self.assertEqual((entry.result, entry.metadata["reason"], entry.username), ("denied", "revoked", "alice"))
