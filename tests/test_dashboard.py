from unittest import mock

from django.test import TestCase
from django.urls import reverse

from backend.models import MCPServer
from backend.services.health import ComponentHealth

from .factories import create_server, create_user


class DashboardTests(TestCase):
    def setUp(self):
        self.user = create_user()

    def test_anonymous_user_is_redirected_to_login(self):
        url = reverse("frontend:dashboard")
        self.assertRedirects(self.client.get(url), f"{reverse('frontend:login')}?next={url}")

    def test_dashboard_renders_for_logged_in_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/index.html")

    def test_active_user_count_excludes_inactive_users(self):
        create_user("bob")
        create_user("carol", is_active=False)
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard"))
        self.assertEqual(response.context["active_user_count"], 2)

    def test_mcp_counts_come_from_the_registry(self):
        create_server("A", "alpha", status=MCPServer.Status.RUNNING)
        create_server("B", "bravo", status=MCPServer.Status.STOPPED, health_status=MCPServer.HealthStatus.UNHEALTHY)
        create_server("C", "charlie")
        self.client.force_login(self.user)
        counts = self.client.get(reverse("frontend:dashboard")).context["counts"]
        self.assertEqual((counts.total, counts.running, counts.stopped, counts.unhealthy), (3, 1, 1, 1))

    def test_counts_only_include_servers_the_user_can_see(self):
        create_server("Open", "open-mcp")
        create_server("Secret", "secret-mcp", access_policy=MCPServer.AccessPolicy.RESTRICTED)
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard"))
        self.assertEqual(response.context["counts"].total, 1)
        self.assertNotContains(response, "Secret")

    def test_admin_navigation_hidden_from_normal_users(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard"))
        self.assertNotContains(response, "Administration")
        self.assertNotContains(response, reverse("admin:index"))

    def test_admin_navigation_shown_to_staff(self):
        staff = create_user("admin", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("frontend:dashboard"))
        self.assertContains(response, "Administration")
        self.assertContains(response, reverse("admin:index"))


class SystemStatusPartialTests(TestCase):
    def setUp(self):
        self.url = reverse("frontend:system-status")

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_reports_healthy_database(self):
        self.client.force_login(create_user())
        response = self.client.get(self.url, headers={"HX-Request": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Healthy")
        self.assertNotContains(response, "<html")

    def test_reports_database_outage(self):
        self.client.force_login(create_user())
        with mock.patch("backend.services.health.check_database", return_value=ComponentHealth(status="error")):
            response = self.client.get(self.url)
        self.assertContains(response, "Unavailable")
