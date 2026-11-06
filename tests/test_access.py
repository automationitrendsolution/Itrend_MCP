"""Permission levels and who can see / connect to / manage / administer an MCP."""

from django.core.exceptions import ValidationError
from django.test import TestCase

from backend.models import MCPActivityLog, MCPPermission, MCPServer, PermissionLevel
from backend.services import access

from .factories import create_server, create_service_account, create_user, grant, restricted_server

L = PermissionLevel


class LevelOrderTests(TestCase):
    def test_levels_are_ordered(self):
        self.assertEqual([L.rank(level) for level in (None, L.VIEW, L.CONNECT, L.MANAGE, L.ADMIN)], [0, 1, 2, 3, 4])
        self.assertEqual(L.rank("nonsense"), 0)


class EffectiveLevelTests(TestCase):
    def setUp(self):
        self.open = create_server("Open", "open-mcp")
        self.secret = restricted_server()
        self.alice = create_user()

    def test_staff_are_admin_everywhere(self):
        admin = create_user("boss", is_staff=True)
        self.assertEqual(access.effective_level(admin, self.secret), L.ADMIN)

    def test_open_mcp_gives_people_connect(self):
        self.assertEqual(access.effective_level(self.alice, self.open), L.CONNECT)

    def test_restricted_mcp_gives_nothing_without_a_grant(self):
        self.assertIsNone(access.effective_level(self.alice, self.secret))

    def test_explicit_grant(self):
        grant(self.alice, self.secret, L.VIEW)
        self.assertEqual(access.effective_level(self.alice, self.secret), L.VIEW)
        self.assertTrue(access.can_view(self.alice, self.secret))
        self.assertFalse(access.can_connect(self.alice, self.secret))

    def test_highest_of_grant_and_policy_wins(self):
        grant(self.alice, self.open, L.VIEW)
        self.assertEqual(access.effective_level(self.alice, self.open), L.CONNECT)
        MCPPermission.objects.filter(user=self.alice).update(permission_level=L.MANAGE)
        self.assertEqual(access.effective_level(self.alice, self.open), L.MANAGE)

    def test_service_accounts_do_not_get_the_all_employees_policy(self):
        service = create_service_account()
        self.assertIsNone(access.effective_level(service, self.open))
        grant(service, self.open, L.CONNECT)
        self.assertEqual(access.effective_level(service, self.open), L.CONNECT)

    def test_inactive_users_have_no_access(self):
        grant(self.alice, self.secret, L.ADMIN)
        self.alice.is_active = False
        self.alice.save()
        self.assertIsNone(access.effective_level(self.alice, self.open))
        self.assertIsNone(access.effective_level(self.alice, self.secret))

    def test_level_helpers(self):
        grant(self.alice, self.secret, L.MANAGE)
        self.assertTrue(access.can_manage(self.alice, self.secret))
        self.assertFalse(access.can_admin(self.alice, self.secret))


class VisibilityTests(TestCase):
    def setUp(self):
        self.open = create_server("Open", "open-mcp")
        self.secret = restricted_server()
        self.other = restricted_server("Other", "other-mcp")

    def visible(self, user):
        return set(MCPServer.objects.visible_to(user))

    def test_granted_restricted_server_becomes_visible(self):
        alice = create_user()
        grant(alice, self.secret, L.VIEW)
        self.assertEqual(self.visible(alice), {self.open, self.secret})

    def test_grants_to_others_do_not_leak(self):
        alice, bob = create_user(), create_user("bob")
        grant(bob, self.secret, L.ADMIN)
        self.assertEqual(self.visible(alice), {self.open})

    def test_service_account_sees_only_granted(self):
        service = create_service_account()
        self.assertEqual(self.visible(service), set())
        grant(service, self.other, L.CONNECT)
        self.assertEqual(self.visible(service), {self.other})

    def test_no_duplicates_when_open_and_granted(self):
        alice = create_user()
        grant(alice, self.open, L.MANAGE)
        self.assertEqual(MCPServer.objects.visible_to(alice).filter(pk=self.open.pk).count(), 1)

    def test_connectable_servers_excludes_external(self):
        create_server("Ext", "ext-mcp", deployment_type=MCPServer.DeploymentType.EXTERNAL, endpoint="https://x.example.com/mcp")
        self.assertEqual({s.slug for s in access.connectable_servers(create_user())}, {"open-mcp"})


class GrantTests(TestCase):
    def setUp(self):
        self.admin = create_user("boss", is_staff=True)
        self.alice = create_user()
        self.server = restricted_server()

    def test_grant_creates_then_replaces(self):
        access.grant_permission(server=self.server, user=self.alice, level=L.VIEW, granted_by=self.admin)
        access.grant_permission(server=self.server, user=self.alice, level=L.CONNECT, granted_by=self.admin)
        permission = MCPPermission.objects.get()
        self.assertEqual(permission.permission_level, L.CONNECT)
        self.assertEqual(permission.granted_by, self.admin)
        entries = MCPActivityLog.objects.filter(action="permission_granted").order_by("id")
        self.assertEqual([e.metadata["previous_level"] for e in entries], [None, "view"])

    def test_granting_the_same_level_again_is_not_logged_twice(self):
        for _ in range(2):
            access.grant_permission(server=self.server, user=self.alice, level=L.VIEW, granted_by=self.admin)
        self.assertEqual(MCPActivityLog.objects.filter(action="permission_granted").count(), 1)

    def test_invalid_level_and_inactive_user_are_rejected(self):
        with self.assertRaises(ValidationError):
            access.grant_permission(server=self.server, user=self.alice, level="owner", granted_by=self.admin)
        self.alice.is_active = False
        with self.assertRaises(ValidationError):
            access.grant_permission(server=self.server, user=self.alice, level=L.VIEW, granted_by=self.admin)

    def test_revoke_deletes_and_logs(self):
        permission = access.grant_permission(server=self.server, user=self.alice, level=L.VIEW, granted_by=self.admin)
        access.revoke_permission(permission, revoked_by=self.admin)
        self.assertFalse(MCPPermission.objects.exists())
        entry = MCPActivityLog.objects.get(action="permission_revoked")
        self.assertEqual((entry.username, entry.mcp_slug, entry.metadata["target_user"]), ("boss", "secret", "alice"))
