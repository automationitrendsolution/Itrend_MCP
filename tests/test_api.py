from unittest import mock

from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient


class HealthApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("backend:health")

    def test_url_is_versioned(self):
        self.assertEqual(self.url, "/api/v1/health/")

    def test_healthy_platform_returns_200_without_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    def test_database_failure_returns_503_without_details(self):
        with mock.patch("backend.services.health.connection.cursor", side_effect=DatabaseError("password authentication failed for user x")):
            with self.assertLogs("backend.services.health", level="ERROR"):
                response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "degraded", "database": "error"})
        self.assertNotIn("password", response.content.decode())

    def test_only_get_is_allowed(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)


class ApiDefaultsTests(TestCase):
    def test_unknown_api_route_returns_404(self):
        self.assertEqual(APIClient().get("/api/v1/does-not-exist/").status_code, 404)

    def test_api_requires_authentication_by_default(self):
        from rest_framework.settings import api_settings
        from rest_framework.permissions import IsAuthenticated

        self.assertEqual(api_settings.DEFAULT_PERMISSION_CLASSES, [IsAuthenticated])
