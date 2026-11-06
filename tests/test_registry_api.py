from django.urls import reverse
from rest_framework.test import APITestCase

from backend.models import MCPServer

from .factories import create_server, create_user

LIST_URL = "/api/v1/mcps/"


def detail_url(pk):
    return f"{LIST_URL}{pk}/"


class AnonymousAccessTests(APITestCase):
    def test_list_requires_login(self):
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")


class ReadAccessTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(create_user())
        self.open = create_server("Open", "open-mcp", internal_endpoint="http://mcp-open:8000/mcp", configuration={"max_rows": 5})
        self.hidden = create_server("Hidden", "hidden-mcp", access_policy=MCPServer.AccessPolicy.RESTRICTED)

    def test_url_names(self):
        self.assertEqual(reverse("backend:mcp-list"), LIST_URL)

    def test_list_is_paginated_and_shows_visible_only(self):
        body = self.client.get(LIST_URL).json()
        self.assertEqual(body["count"], 1)
        self.assertEqual([s["slug"] for s in body["results"]], ["open-mcp"])

    def test_internal_fields_hidden_from_normal_users(self):
        server = self.client.get(detail_url(self.open.pk)).json()
        self.assertNotIn("internal_endpoint", server)
        self.assertNotIn("configuration", server)
        self.assertEqual(server["connection_url"], self.open.connection_url)

    def test_restricted_server_is_404(self):
        response = self.client.get(detail_url(self.hidden.pk))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_filters(self):
        create_server("Running", "running-mcp", status=MCPServer.Status.RUNNING)
        slugs = lambda **q: [s["slug"] for s in self.client.get(LIST_URL, q).json()["results"]]
        self.assertEqual(slugs(status="running"), ["running-mcp"])
        self.assertEqual(slugs(search="open"), ["open-mcp"])

    def test_normal_users_cannot_write(self):
        create = self.client.post(LIST_URL, {"name": "X", "slug": "xyz", "deployment_type": "zip"}, format="json")
        patch = self.client.patch(detail_url(self.open.pk), {"name": "Changed"}, format="json")
        delete = self.client.delete(detail_url(self.open.pk))
        self.assertEqual([create.status_code, patch.status_code, delete.status_code], [403, 403, 403])
        self.assertEqual(MCPServer.objects.count(), 2)


class AdminTests(APITestCase):
    def setUp(self):
        self.admin = create_user("admin", is_staff=True)
        self.client.force_authenticate(self.admin)

    def test_staff_see_internal_fields(self):
        server = create_server(internal_endpoint="http://mcp-mysql:8000/mcp")
        self.assertEqual(self.client.get(detail_url(server.pk)).json()["internal_endpoint"], "http://mcp-mysql:8000/mcp")

    def test_api_is_read_only_even_for_staff(self):
        # MCPs are added and changed through code (mcp_servers/<slug>/mcp.json).
        server = create_server()
        responses = [
            self.client.post(LIST_URL, {"name": "X", "slug": "xyz", "deployment_type": "zip"}, format="json"),
            self.client.patch(detail_url(server.pk), {"status": "running"}, format="json"),
            self.client.put(detail_url(server.pk), {}, format="json"),
            self.client.delete(detail_url(server.pk)),
        ]
        self.assertEqual([r.status_code for r in responses], [403, 403, 403, 403])
        server.refresh_from_db()
        self.assertEqual(server.status, "pending")
        self.assertEqual(MCPServer.objects.count(), 1)
