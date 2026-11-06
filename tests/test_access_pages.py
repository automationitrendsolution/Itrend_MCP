"""Website pages for tokens, permissions, users and the activity log."""

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from backend.models import AccessToken, MCPActivityLog, MCPPermission, PermissionLevel, ServiceAccount

from .factories import create_server, create_service_account, create_token, create_user, grant, restricted_server


class TokenPageTests(TestCase):
    def setUp(self):
        self.alice = create_user()
        self.server = create_server()
        self.client.force_login(self.alice)

    def test_generate_token_shows_it_once(self):
        url = reverse("frontend:token-create")
        self.assertContains(self.client.get(url), "Generate token")
        response = self.client.post(url, {"mcp_server": self.server.pk, "name": "Laptop", "expires_in_days": 30})
        self.assertEqual(response.status_code, 200)
        token = AccessToken.objects.get()
        self.assertContains(response, "shown only once")
        self.assertIn("no-store", response["Cache-Control"])
        plaintext = response.context["plaintext"]
        self.assertTrue(plaintext.startswith(token.token_prefix))

        listing = self.client.get(reverse("frontend:token-list"))
        self.assertContains(listing, token.token_prefix)
        self.assertNotContains(listing, plaintext)

    def test_cannot_pick_an_mcp_without_connect(self):
        secret = restricted_server()
        response = self.client.post(reverse("frontend:token-create"), {"mcp_server": secret.pk, "name": "x", "expires_in_days": 30})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AccessToken.objects.exists())
        self.assertTrue(response.context["form"].errors)

    def test_revoke(self):
        token, _ = create_token(self.alice, self.server)
        response = self.client.post(reverse("frontend:token-revoke", args=[token.pk]))
        self.assertRedirects(response, reverse("frontend:token-list"))
        token.refresh_from_db()
        self.assertEqual(token.state, "revoked")

    def test_cannot_revoke_someone_elses_token(self):
        token, _ = create_token(create_user("bob"), self.server)
        self.assertEqual(self.client.post(reverse("frontend:token-revoke", args=[token.pk])).status_code, 403)

    def test_revoke_ignores_unsafe_next_url(self):
        token, _ = create_token(self.alice, self.server)
        response = self.client.post(reverse("frontend:token-revoke", args=[token.pk]), {"next": "https://evil.example.com/"})
        self.assertRedirects(response, reverse("frontend:token-list"))

    def test_people_cannot_create_tokens_for_service_accounts(self):
        service = create_service_account()
        self.assertEqual(self.client.get(reverse("frontend:user-token-create", args=[service.pk])).status_code, 403)

    def test_mcp_detail_offers_tokens(self):
        response = self.client.get(reverse("frontend:mcp-detail", args=[self.server.pk]))
        self.assertContains(response, "Your access: <strong>Connect</strong>", html=False)
        self.assertContains(response, f"{reverse('frontend:token-create')}?mcp={self.server.pk}")
        self.assertNotContains(response, "Who has access")


class McpPermissionPageTests(TestCase):
    def setUp(self):
        self.alice, self.bob = create_user(), create_user("bob")
        self.server = restricted_server()
        self.client.force_login(self.alice)

    def test_view_level_sees_but_cannot_connect(self):
        grant(self.alice, self.server, PermissionLevel.VIEW)
        response = self.client.get(reverse("frontend:mcp-detail", args=[self.server.pk]))
        self.assertContains(response, "Ask an administrator for <strong>Connect</strong>")
        self.assertEqual([t[0] for t in response.context["tabs"]], ["overview", "manual"])
        self.assertEqual(self.client.get(reverse("frontend:mcp-manual", args=[self.server.pk])).status_code, 200)

    def test_manage_level_cannot_start_stop_or_see_access_tab(self):
        grant(self.alice, self.server, PermissionLevel.MANAGE)
        self.assertEqual(self.client.get(reverse("frontend:mcp-access", args=[self.server.pk])).status_code, 403)
        url = reverse("frontend:mcp-control", args=[self.server.pk, "start"])
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_mcp_admin_sees_logs_tab_without_server_output(self):
        grant(self.alice, self.server, PermissionLevel.ADMIN)
        response = self.client.get(reverse("frontend:mcp-logs", args=[self.server.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Who did what on this MCP")
        self.assertNotContains(response, "Server log")  # process output: platform administrators only

    def test_mcp_admin_can_grant_and_remove_on_that_mcp(self):
        grant(self.alice, self.server, PermissionLevel.ADMIN)
        detail = reverse("frontend:mcp-access", args=[self.server.pk])
        self.assertContains(self.client.get(detail), "People and systems with access")

        response = self.client.post(reverse("frontend:permission-grant"), {
            "user": self.bob.pk, "mcp_server": self.server.pk, "permission_level": "connect", "next": detail,
        })
        self.assertRedirects(response, detail)
        permission = MCPPermission.objects.get(user=self.bob)
        self.assertEqual(permission.permission_level, "connect")

        self.client.post(reverse("frontend:permission-revoke", args=[permission.pk]))
        self.assertFalse(MCPPermission.objects.filter(user=self.bob).exists())

    def test_non_admins_cannot_grant(self):
        grant(self.alice, self.server, PermissionLevel.MANAGE)
        response = self.client.post(reverse("frontend:permission-grant"), {
            "user": self.alice.pk, "mcp_server": self.server.pk, "permission_level": "admin",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(MCPPermission.objects.get().permission_level, "manage")

    def test_cannot_remove_permission_on_another_mcp(self):
        other = restricted_server("Other", "other")
        permission = grant(self.bob, other, PermissionLevel.VIEW)
        grant(self.alice, self.server, PermissionLevel.ADMIN)
        self.assertEqual(self.client.post(reverse("frontend:permission-revoke", args=[permission.pk])).status_code, 403)


class AdminPagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = create_user("boss", is_staff=True)
        self.client.force_login(self.admin)

    def test_admin_pages_render(self):
        alice = create_user()
        for name, args in (
            ("frontend:user-list", []), ("frontend:user-detail", [alice.pk]), ("frontend:permission-list", []),
            ("frontend:activity-list", []), ("frontend:service-account-create", []),
        ):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_admin_pages_are_forbidden_to_others(self):
        self.client.force_login(create_user())
        for name in ("frontend:user-list", "frontend:permission-list", "frontend:activity-list", "frontend:service-account-create"):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_create_service_account_and_its_token(self):
        response = self.client.post(reverse("frontend:service-account-create"), {"name": "ppc-server", "description": "PPC"})
        account = ServiceAccount.objects.get()
        self.assertRedirects(response, reverse("frontend:user-detail", args=[account.user_id]))
        self.assertEqual(account.user.username, "svc-ppc-server")
        self.assertFalse(account.user.has_usable_password())

        server = create_server()
        grant(account.user, server, PermissionLevel.CONNECT)
        response = self.client.post(
            reverse("frontend:user-token-create", args=[account.user_id]),
            {"mcp_server": server.pk, "name": "PPC production", "expires_in_days": 365},
        )
        self.assertContains(response, "for svc-ppc-server")
        token = AccessToken.objects.get()
        self.assertEqual((token.user, token.created_by), (account.user, self.admin))

    def test_service_account_name_rules(self):
        response = self.client.post(reverse("frontend:service-account-create"), {"name": "Bad Name!"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ServiceAccount.objects.exists())

    def test_service_accounts_cannot_log_in(self):
        create_service_account()
        self.client.logout()
        response = self.client.post(reverse("frontend:login"), {"username": "svc-ppc-server", "password": ""})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_deactivate_and_reactivate(self):
        alice = create_user()
        url = reverse("frontend:user-set-active", args=[alice.pk])
        self.client.post(url, {"active": "0"})
        alice.refresh_from_db()
        self.assertFalse(alice.is_active)
        self.client.post(url, {"active": "1"})
        alice.refresh_from_db()
        self.assertTrue(alice.is_active)
        self.assertEqual(
            list(MCPActivityLog.objects.filter(action__startswith="user_").order_by("id").values_list("action", flat=True)),
            ["user_deactivated", "user_activated"],
        )

    def test_cannot_deactivate_yourself(self):
        self.client.post(reverse("frontend:user-set-active", args=[self.admin.pk]), {"active": "0"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_deactivated_user_session_stops_working(self):
        alice = create_user()
        other = self.client_class()
        other.force_login(alice)
        self.client.post(reverse("frontend:user-set-active", args=[alice.pk]), {"active": "0"})
        response = other.get(reverse("frontend:dashboard"))
        self.assertRedirects(response, f"{reverse('frontend:login')}?next=/")

    def test_activity_filters(self):
        MCPActivityLog.objects.create(action="login", username="alice")
        MCPActivityLog.objects.create(action="login_failed", username="mallory", result="failure")
        MCPActivityLog.objects.create(action="token_created", username="bob", metadata={"token_name": "Laptop"})
        page = lambda **q: [e.username for e in self.client.get(reverse("frontend:activity-list"), q).context["page"].object_list]
        self.assertEqual(page(problems="1"), ["mallory"])
        self.assertEqual(page(kind="signins")[:2], ["mallory", "alice"])  # then the test admin's own login
        self.assertEqual(page(kind="access"), ["bob"])
        htmx = self.client.get(reverse("frontend:activity-list"), {"q": "alice"}, HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(htmx, "activity/partials/timeline.html")
        self.assertEqual([e.username for e in htmx.context["page"].object_list], ["alice"])

    def test_activity_is_written_as_sentences(self):
        MCPActivityLog.objects.create(action="login_failed", username="mallory", result="denied", metadata={"reason": "locked_out"})
        MCPActivityLog.objects.create(
            action="mcp_connection", username="bob", mcp_slug="mysql", result="denied", metadata={"reason": "expired"}
        )
        response = self.client.get(reverse("frontend:activity-list"))
        self.assertContains(response, "was blocked from signing in after too many wrong passwords")
        self.assertContains(response, "the token has expired")
        self.assertContains(response, "Today")
        self.assertEqual(response.context["recent"]["refused"], 1)
        self.assertEqual(response.context["recent"]["failed_logins"], 1)
