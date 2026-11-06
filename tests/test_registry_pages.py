from django.test import TestCase
from django.urls import reverse

from backend.models import MCPServer

from .factories import create_server, create_user


class ListPageTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        create_server("MySQL MCP", "mysql", status=MCPServer.Status.RUNNING)
        create_server("Power BI MCP", "powerbi", deployment_type=MCPServer.DeploymentType.GITHUB)
        create_server("Hidden MCP", "hidden", access_policy=MCPServer.AccessPolicy.RESTRICTED)
        self.url = reverse("frontend:mcp-list")

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_lists_visible_servers_only(self):
        response = self.client.get(self.url)
        self.assertContains(response, "MySQL MCP")
        self.assertContains(response, "Power BI MCP")
        self.assertNotContains(response, "Hidden MCP")

    def test_search_and_filters(self):
        # The sidebar lists every visible MCP, so check the table's rows, not the whole page.
        names = lambda **q: [s.name for s in self.client.get(self.url, q).context["page"].object_list]
        self.assertEqual(names(q="power"), ["Power BI MCP"])
        self.assertEqual(names(status="running"), ["MySQL MCP"])
        self.assertEqual(names(type="github"), ["Power BI MCP"])

    def test_htmx_request_returns_only_the_table(self):
        response = self.client.get(self.url, {"q": "mysql"}, headers={"HX-Request": "true"})
        self.assertTemplateUsed(response, "mcps/partials/table.html")
        self.assertTemplateNotUsed(response, "mcps/list.html")
        self.assertNotContains(response, "<html")

    def test_rescan_button_hidden_from_normal_users(self):
        self.assertNotContains(self.client.get(self.url), reverse("frontend:mcp-rescan"))

    def test_sidebar_lists_visible_mcps(self):
        response = self.client.get(reverse("frontend:dashboard"))
        self.assertEqual([s.name for s in response.context["sidebar_mcps"]], ["MySQL MCP", "Power BI MCP"])
        self.assertContains(response, 'id="mcpSubnav"')

    def test_pagination(self):
        for i in range(25):
            create_server(f"Bulk {i:02}", f"bulk-{i:02}")
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["page"].object_list), 20)
        self.assertContains(response, "Next")


class DetailPageTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)

    def test_shows_connection_url_and_copy_button(self):
        server = create_server(slug="mysql")
        response = self.client.get(reverse("frontend:mcp-detail", args=[server.pk]))
        self.assertContains(response, server.connection_url)
        self.assertContains(response, "data-copy-target")

    def test_restricted_server_returns_404_not_403(self):
        server = create_server(access_policy=MCPServer.AccessPolicy.RESTRICTED)
        self.assertEqual(self.client.get(reverse("frontend:mcp-detail", args=[server.pk])).status_code, 404)

    def test_internal_settings_hidden_from_normal_users(self):
        server = create_server(internal_endpoint="http://mcp-mysql:8000/mcp", configuration={"max_rows": 5})
        response = self.client.get(reverse("frontend:mcp-detail", args=[server.pk]))
        self.assertNotContains(response, "mcp-mysql:8000")
        self.assertNotContains(response, "max_rows")
        self.assertNotContains(response, reverse("frontend:mcp-delete", args=[server.pk]))

    def test_internal_settings_shown_to_staff(self):
        self.client.force_login(create_user("admin", is_staff=True))
        server = create_server(internal_endpoint="http://mcp-mysql:8000/mcp")
        response = self.client.get(reverse("frontend:mcp-detail", args=[server.pk]))
        self.assertContains(response, "mcp-mysql:8000")
        self.assertContains(response, reverse("frontend:mcp-delete", args=[server.pk]))

    def test_external_server_shows_protection_warning(self):
        server = create_server(slug="ext", deployment_type=MCPServer.DeploymentType.EXTERNAL, endpoint="https://ext.example.com/mcp")
        response = self.client.get(reverse("frontend:mcp-detail", args=[server.pk]))
        self.assertContains(response, "runs outside the platform")


class ManagePermissionTests(TestCase):
    """Normal users must not be able to change the registry by calling URLs directly."""

    def setUp(self):
        self.server = create_server()
        self.client.force_login(create_user())

    def test_start_stop_forbidden(self):
        for action in ("start", "stop", "restart"):
            url = reverse("frontend:mcp-control", args=[self.server.pk, action])
            self.assertEqual(self.client.post(url).status_code, 403)

    def test_rescan_and_access_policy_forbidden(self):
        self.assertEqual(self.client.post(reverse("frontend:mcp-rescan")).status_code, 403)
        url = reverse("frontend:mcp-access-policy", args=[self.server.pk])
        self.assertEqual(self.client.post(url, {"access_policy": "restricted"}).status_code, 403)

    def test_logs_and_access_tabs_forbidden(self):
        self.assertEqual(self.client.get(reverse("frontend:mcp-logs", args=[self.server.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("frontend:mcp-log-output", args=[self.server.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("frontend:mcp-access", args=[self.server.pk])).status_code, 403)

    def test_delete_forbidden(self):
        self.assertEqual(self.client.post(reverse("frontend:mcp-delete", args=[self.server.pk])).status_code, 403)
        self.assertTrue(MCPServer.objects.exists())


class AdminTests(TestCase):
    def setUp(self):
        self.admin = create_user("admin", is_staff=True)
        self.client.force_login(self.admin)

    def test_old_add_and_edit_pages_are_gone(self):
        server = create_server()
        self.assertEqual(self.client.get("/mcps/add/").status_code, 404)
        self.assertEqual(self.client.get(f"/mcps/{server.pk}/edit/").status_code, 404)

    def test_change_access_policy(self):
        server = create_server(access_policy=MCPServer.AccessPolicy.RESTRICTED)
        url = reverse("frontend:mcp-access-policy", args=[server.pk])
        self.assertRedirects(self.client.post(url, {"access_policy": "all_users"}), reverse("frontend:mcp-access", args=[server.pk]))
        server.refresh_from_db()
        self.assertEqual(server.access_policy, "all_users")
        self.client.post(url, {"access_policy": "nonsense"})
        server.refresh_from_db()
        self.assertEqual(server.access_policy, "all_users")

    def test_delete_requires_post(self):
        server = create_server()
        self.assertEqual(self.client.get(reverse("frontend:mcp-delete", args=[server.pk])).status_code, 405)
        response = self.client.post(reverse("frontend:mcp-delete", args=[server.pk]))
        self.assertRedirects(response, reverse("frontend:mcp-list"))
        self.assertFalse(MCPServer.objects.exists())

