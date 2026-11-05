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
        self.assertNotContains(self.client.get(self.url, {"q": "power"}), "MySQL MCP")
        self.assertNotContains(self.client.get(self.url, {"status": "running"}), "Power BI MCP")
        self.assertNotContains(self.client.get(self.url, {"type": "github"}), "MySQL MCP")

    def test_htmx_request_returns_only_the_table(self):
        response = self.client.get(self.url, {"q": "mysql"}, headers={"HX-Request": "true"})
        self.assertTemplateUsed(response, "mcps/partials/table.html")
        self.assertTemplateNotUsed(response, "mcps/list.html")
        self.assertNotContains(response, "<html")

    def test_add_button_hidden_from_normal_users(self):
        self.assertNotContains(self.client.get(self.url), reverse("frontend:mcp-create"))

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
        self.assertNotContains(response, "deleteModal")

    def test_internal_settings_shown_to_staff(self):
        self.client.force_login(create_user("admin", is_staff=True))
        server = create_server(internal_endpoint="http://mcp-mysql:8000/mcp")
        response = self.client.get(reverse("frontend:mcp-detail", args=[server.pk]))
        self.assertContains(response, "mcp-mysql:8000")
        self.assertContains(response, "deleteModal")

    def test_external_server_shows_protection_warning(self):
        server = create_server(slug="ext", deployment_type=MCPServer.DeploymentType.EXTERNAL, endpoint="https://ext.example.com/mcp")
        response = self.client.get(reverse("frontend:mcp-detail", args=[server.pk]))
        self.assertContains(response, "runs outside the platform")


class ManagePermissionTests(TestCase):
    """Normal users must not be able to change the registry by calling URLs directly."""

    def setUp(self):
        self.server = create_server()
        self.client.force_login(create_user())

    def test_create_page_forbidden(self):
        self.assertEqual(self.client.get(reverse("frontend:mcp-create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("frontend:mcp-create"), {}).status_code, 403)

    def test_edit_forbidden(self):
        self.assertEqual(self.client.get(reverse("frontend:mcp-edit", args=[self.server.pk])).status_code, 403)

    def test_delete_forbidden(self):
        self.assertEqual(self.client.post(reverse("frontend:mcp-delete", args=[self.server.pk])).status_code, 403)
        self.assertTrue(MCPServer.objects.exists())

    def test_slug_check_forbidden(self):
        self.assertEqual(self.client.get(reverse("frontend:mcp-slug-check"), {"slug": "x"}).status_code, 403)


class CreateEditDeleteTests(TestCase):
    def setUp(self):
        self.admin = create_user("admin", is_staff=True)
        self.client.force_login(self.admin)

    def post_create(self, **overrides):
        data = {
            "deployment_type": "zip",
            "name": "ITrend MCP",
            "slug": "itrend",
            "description": "",
            "transport_type": "streamable_http",
            "endpoint": "",
            "internal_endpoint": "",
            "access_policy": "restricted",
            "configuration": "",
            **overrides,
        }
        return self.client.post(reverse("frontend:mcp-create"), data)

    def test_create_hosted_server(self):
        response = self.post_create()
        server = MCPServer.objects.get(slug="itrend")
        self.assertRedirects(response, reverse("frontend:mcp-detail", args=[server.pk]))
        self.assertEqual(server.created_by, self.admin)
        self.assertEqual(server.configuration, {})

    def test_create_external_server(self):
        self.post_create(deployment_type="external", slug="ext", endpoint="https://ext.example.com/mcp")
        self.assertEqual(MCPServer.objects.get(slug="ext").endpoint, "https://ext.example.com/mcp")

    def test_create_shows_errors(self):
        response = self.post_create(slug="admin")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "reserved")
        self.assertFalse(MCPServer.objects.exists())

    def test_external_without_endpoint_is_rejected(self):
        response = self.post_create(deployment_type="external")
        self.assertContains(response, "needs the URL")

    def test_secret_in_configuration_is_rejected(self):
        response = self.post_create(configuration='{"db_password": "hunter2"}')
        self.assertContains(response, "looks like a secret")
        self.assertFalse(MCPServer.objects.exists())

    def test_create_preselects_type_from_query(self):
        response = self.client.get(reverse("frontend:mcp-create"), {"type": "github"})
        self.assertEqual(response.context["form"].initial["deployment_type"], "github")

    def test_edit_cannot_change_slug_or_type(self):
        server = create_server(slug="mysql")
        self.client.post(
            reverse("frontend:mcp-edit", args=[server.pk]),
            {
                "deployment_type": "external", "name": "Renamed", "slug": "hacked", "description": "",
                "transport_type": "streamable_http", "endpoint": "", "internal_endpoint": "",
                "access_policy": "all_users", "configuration": "{}", "status": "running", "health_status": "healthy",
            },
        )
        server.refresh_from_db()
        self.assertEqual((server.name, server.slug, server.deployment_type), ("Renamed", "mysql", "zip"))
        self.assertEqual(server.status, "running")

    def test_delete_requires_post(self):
        server = create_server()
        self.assertEqual(self.client.get(reverse("frontend:mcp-delete", args=[server.pk])).status_code, 405)
        response = self.client.post(reverse("frontend:mcp-delete", args=[server.pk]))
        self.assertRedirects(response, reverse("frontend:mcp-list"))
        self.assertFalse(MCPServer.objects.exists())

    def test_slug_check(self):
        create_server(slug="mysql")
        check = lambda slug: self.client.get(reverse("frontend:mcp-slug-check"), {"slug": slug}).content.decode()
        self.assertIn("Available", check("powerbi"))
        self.assertIn("already used", check("mysql"))
        self.assertIn("reserved", check("admin"))
        self.assertIn("lowercase", check("Bad_Slug"))
