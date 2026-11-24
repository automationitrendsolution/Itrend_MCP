"""Switching parts of an MCP (e.g. databases) on per person (backend/services/items.py)."""

import io
import json
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from backend.models import MCPActivityLog, MCPItemAccess, MCPPermission, MCPServer, PermissionLevel
from backend.services import access, catalog, items, tokens

from .factories import create_user, grant
from .test_local_mcps import FolderTestCase

KEY = "introspection-" + "k" * 40
LISTING = {"label": "Databases", "items": [{"key": "powerbi", "label": "Power BI"}, {"key": "sales", "label": "Sales"}]}


def fake_urlopen(payload):
    def opener(url, timeout=None):
        response = io.BytesIO(json.dumps(payload).encode())
        response.status = 200  # the page's health check may read it too, if something runs on the port
        response.__enter__ = lambda self=response: self
        response.__exit__ = lambda *args: False
        return response

    return opener


@override_settings(MCP_INTROSPECTION_KEY=KEY)
class ItemAccessTests(FolderTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.add_mcp("mysql", {"name": "MySQL MCP", "command": ["x"], "port": 8101, "access_items_path": "/access-items"})
        catalog.sync()
        self.server = MCPServer.objects.get(slug="mysql")
        self.server.internal_endpoint = "http://127.0.0.1:8101/mcp"
        self.server.save()
        self.admin = create_user("boss", is_superuser=True)
        self.alice = create_user("alice")
        grant(self.alice, self.server, PermissionLevel.CONNECT)
        with mock.patch("backend.services.items.urllib.request.urlopen", fake_urlopen(LISTING)):
            self.assertTrue(items.refresh(self.server))

    def introspect(self, user):
        _, plaintext = tokens.create_token(owner=user, server=self.server, name="t", expires_in_days=30, created_by=user)
        response = self.client.post("/api/v1/tokens/introspect/", {"token": plaintext, "mcp": "mysql"},
                                    content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {KEY}")
        return response.json()["items"]

    def toggle(self, actor, person, key, on):
        self.client.force_login(actor)
        url = reverse("frontend:mcp-item-toggle", args=[self.server.pk, person.pk, key])
        return self.client.post(url, {"on": "1" if on else "0"}, HTTP_HX_REQUEST="true")

    def test_manifest_and_listing(self):
        self.assertEqual(catalog.manifest_for(self.server).access_items_url, "http://127.0.0.1:8101/access-items")
        self.assertEqual([i["key"] for i in items.items_of(self.server)], ["powerbi", "sales"])

    def test_nothing_until_switched_on(self):
        self.assertEqual(self.introspect(self.alice), [])

    def test_superusers_get_everything(self):
        self.assertEqual(self.introspect(self.admin), ["*"])

    def test_switch_on_and_off_reaches_the_token_check(self):
        response = self.toggle(self.admin, self.alice, "powerbi", True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "checked")
        self.assertEqual(self.introspect(self.alice), ["powerbi"])
        self.toggle(self.admin, self.alice, "powerbi", False)
        self.assertEqual(self.introspect(self.alice), [])
        actions = list(MCPActivityLog.objects.filter(metadata__item="powerbi").values_list("action", flat=True))
        self.assertEqual(sorted(actions), ["permission_granted", "permission_revoked"])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("frontend:activity-list")), "the database <strong>Power BI</strong>")

    def test_only_mcp_admins_can_switch(self):
        bob = create_user("bob")
        grant(bob, self.server, PermissionLevel.CONNECT)
        self.assertEqual(self.toggle(bob, self.alice, "powerbi", True).status_code, 403)
        mcp_admin = create_user("carol")
        grant(mcp_admin, self.server, PermissionLevel.ADMIN)
        self.assertEqual(self.toggle(mcp_admin, self.alice, "powerbi", True).status_code, 200)

    def test_unknown_part_is_refused(self):
        self.assertEqual(self.toggle(self.admin, self.alice, "secret", True).status_code, 403)
        self.assertFalse(MCPItemAccess.objects.exists())

    def test_removing_access_clears_the_switches(self):
        self.toggle(self.admin, self.alice, "sales", True)
        access.revoke_permission(MCPPermission.objects.get(user=self.alice), revoked_by=self.admin)
        self.assertFalse(MCPItemAccess.objects.filter(user=self.alice).exists())

    def test_access_page_shows_switches_and_connect_tab_shows_mine(self):
        self.toggle(self.admin, self.alice, "sales", True)
        with mock.patch("backend.services.items.urllib.request.urlopen", fake_urlopen(LISTING)):
            page = self.client.get(reverse("frontend:mcp-access", args=[self.server.pk]))
        self.assertContains(page, "Databases each person may use")
        self.assertContains(page, 'aria-label="Sales for alice"')
        self.client.force_login(self.alice)
        mine = self.client.get(reverse("frontend:mcp-detail", args=[self.server.pk]))
        self.assertContains(mine, "Databases you can use")
        self.assertContains(mine, "<strong>Sales</strong>")

    def test_all_users_policy_lists_everyone(self):
        """With "all users" nobody is added one by one, but everyone must still be switchable."""
        dave = create_user("dave")  # no permission of their own
        self.server.access_policy = MCPServer.AccessPolicy.ALL_USERS
        self.server.save()
        self.client.force_login(self.admin)
        with mock.patch("backend.services.items.urllib.request.urlopen", fake_urlopen(LISTING)):
            page = self.client.get(reverse("frontend:mcp-access", args=[self.server.pk]))
        self.assertContains(page, 'aria-label="Power BI for dave"')
        self.assertEqual(self.toggle(self.admin, dave, "powerbi", True).status_code, 200)
        self.assertEqual(self.introspect(dave), ["powerbi"])

    def test_bad_listing_is_ignored(self):
        with mock.patch("backend.services.items.urllib.request.urlopen", fake_urlopen({"items": "nope"})):
            self.assertFalse(items.refresh(self.server))
        self.assertEqual(len(items.items_of(self.server)), 2)  # the last good list is kept
