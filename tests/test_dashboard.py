from unittest import mock

from django.test import TestCase
from django.urls import reverse

from backend.services.health import ComponentHealth

from .factories import create_user


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

    def test_mcp_figures_are_not_fabricated(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard"))
        self.assertContains(response, ">Available in Phase 2</div>", count=3)

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
