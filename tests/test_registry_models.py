from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from backend.models import MCPServer
from backend.services import registry

from .factories import create_server, create_user


class SlugValidationTests(TestCase):
    def build(self, slug, **extra):
        return MCPServer(name=f"Server {slug}", slug=slug, deployment_type=MCPServer.DeploymentType.ZIP, **extra)

    def test_valid_slugs(self):
        for slug in ("mysql", "power-bi", "amazon2"):
            self.build(slug).full_clean()

    def test_invalid_slug_formats_are_rejected(self):
        for slug in ("MySQL", "my_sql", "-mysql", "mysql-", "my sql", "1mysql", "ab", "../etc", "x" * 51):
            with self.subTest(slug=slug), self.assertRaises(ValidationError):
                self.build(slug).full_clean()

    def test_reserved_slugs_are_rejected(self):
        for slug in ("admin", "api", "static", "accounts", "mcps"):
            with self.subTest(slug=slug), self.assertRaises(ValidationError):
                self.build(slug).full_clean()

    def test_slug_must_be_unique(self):
        create_server(slug="mysql")
        with self.assertRaises(ValidationError):
            self.build("mysql").full_clean()


class EndpointRulesTests(TestCase):
    def test_external_mcp_requires_endpoint(self):
        server = MCPServer(name="Ext", slug="ext", deployment_type=MCPServer.DeploymentType.EXTERNAL)
        with self.assertRaisesMessage(ValidationError, "needs the URL"):
            server.full_clean()

    def test_hosted_mcp_must_not_have_endpoint(self):
        server = MCPServer(name="Zip", slug="zip-mcp", deployment_type=MCPServer.DeploymentType.ZIP, endpoint="https://x.example.com/")
        with self.assertRaises(ValidationError):
            server.full_clean()

    def test_only_http_and_https_endpoints_are_allowed(self):
        for url in ("ftp://example.com/mcp", "file:///etc/passwd", "javascript:alert(1)"):
            server = MCPServer(name="Ext", slug="ext", deployment_type=MCPServer.DeploymentType.EXTERNAL, endpoint=url)
            with self.subTest(url=url), self.assertRaises(ValidationError):
                server.full_clean()

    @override_settings(MCP_PUBLIC_BASE_URL="https://mcp.itrendsolution.com/")
    def test_hosted_connection_url_is_built_from_slug(self):
        self.assertEqual(create_server(slug="mysql").connection_url, "https://mcp.itrendsolution.com/mysql/")

    def test_external_connection_url_is_its_endpoint(self):
        server = create_server(slug="ext", deployment_type=MCPServer.DeploymentType.EXTERNAL, endpoint="https://ext.example.com/mcp")
        self.assertEqual(server.connection_url, "https://ext.example.com/mcp")


class ConfigurationValidationTests(TestCase):
    def build(self, configuration):
        return MCPServer(name="S", slug="srv", deployment_type=MCPServer.DeploymentType.ZIP, configuration=configuration)

    def test_plain_settings_are_allowed(self):
        self.build({"max_rows": 500, "databases": ["sales", "stock"]}).full_clean()

    def test_secret_looking_keys_are_rejected_at_any_depth(self):
        for config in (
            {"DB_PASSWORD": "x"},
            {"github": {"access_token": "x"}},
            {"items": [{"Api-Key": "x"}]},
            {"client_secret": "x"},
        ):
            with self.subTest(config=config), self.assertRaisesMessage(ValidationError, "looks like a secret"):
                self.build(config).full_clean()

    def test_configuration_must_be_an_object(self):
        with self.assertRaises(ValidationError):
            self.build(["not", "an", "object"]).full_clean()


class VisibilityTests(TestCase):
    def setUp(self):
        self.open = create_server("Open", "open-mcp")
        self.restricted = create_server("Restricted", "restricted-mcp", access_policy=MCPServer.AccessPolicy.RESTRICTED)

    def visible(self, user):
        return set(MCPServer.objects.visible_to(user))

    def test_staff_see_everything(self):
        self.assertEqual(self.visible(create_user("admin", is_staff=True)), {self.open, self.restricted})

    def test_normal_users_see_only_open_servers(self):
        self.assertEqual(self.visible(create_user()), {self.open})

    def test_inactive_users_see_nothing(self):
        self.assertEqual(self.visible(create_user(is_active=False, is_staff=True)), set())

    def test_new_servers_are_restricted_by_default(self):
        server = MCPServer(name="New", slug="new-mcp", deployment_type=MCPServer.DeploymentType.ZIP)
        self.assertEqual(server.access_policy, MCPServer.AccessPolicy.RESTRICTED)


class RegistryServiceTests(TestCase):
    def setUp(self):
        self.admin = create_user("admin", is_staff=True)

    def test_create_records_creator(self):
        server = registry.create_server(
            created_by=self.admin, name="Power BI", slug="powerbi", deployment_type=MCPServer.DeploymentType.GITHUB
        )
        self.assertEqual(server.created_by, self.admin)
        self.assertEqual(server.status, MCPServer.Status.PENDING)

    def test_create_validates(self):
        with self.assertRaises(ValidationError):
            registry.create_server(created_by=self.admin, name="Bad", slug="admin", deployment_type=MCPServer.DeploymentType.ZIP)
        self.assertFalse(MCPServer.objects.exists())

    def test_slug_and_deployment_type_cannot_change(self):
        server = create_server()
        with self.assertRaises(ValueError):
            registry.update_server(server, updated_by=self.admin, slug="renamed")
        with self.assertRaises(ValueError):
            registry.update_server(server, updated_by=self.admin, deployment_type=MCPServer.DeploymentType.EXTERNAL)

    def test_update_changes_other_fields(self):
        server = create_server()
        registry.update_server(server, updated_by=self.admin, description="Sales database", status=MCPServer.Status.RUNNING)
        server.refresh_from_db()
        self.assertEqual((server.description, server.status), ("Sales database", MCPServer.Status.RUNNING))

    def test_delete_removes_deployments_too(self):
        server = create_server()
        server.deployments.create(deployment_type=server.deployment_type)
        registry.delete_server(server, deleted_by=self.admin)
        self.assertFalse(MCPServer.objects.exists())

    def test_list_filters(self):
        create_server("A", "alpha", status=MCPServer.Status.RUNNING)
        create_server("B", "bravo", deployment_type=MCPServer.DeploymentType.GITHUB)
        names = lambda **f: [s.name for s in registry.list_servers(self.admin, **f)]
        self.assertEqual(names(status="running"), ["A"])
        self.assertEqual(names(deployment_type="github"), ["B"])
        self.assertEqual(names(search="brav"), ["B"])
        self.assertEqual(names(status="not-a-status"), ["A", "B"])
