from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from .factories import create_user


class SecurityHeaderTests(TestCase):
    def test_clickjacking_and_sniffing_protection(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_session_cookie_is_http_only(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)


class ErrorPageTests(TestCase):
    @override_settings(DEBUG=False)
    def test_404_page_does_not_leak_debug_information(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertNotContains(response, "URLconf", status_code=404)

    def test_500_template_renders_without_context(self):
        from django.template.loader import render_to_string

        html = render_to_string("500.html")
        self.assertIn("Something went wrong", html)


class AdminAccessTests(TestCase):
    def test_non_staff_user_cannot_open_django_admin(self):
        self.client.force_login(create_user())
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
