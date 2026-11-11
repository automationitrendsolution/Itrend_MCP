"""REST API for tokens, users and token introspection; MANAGE permission over the API."""

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from backend.models import AccessToken, PermissionLevel

from .factories import create_server, create_service_account, create_token, create_user, grant, restricted_server

KEY = "k" * 40
INTROSPECT = "/api/v1/tokens/introspect/"


class TokenApiTests(APITestCase):
    def setUp(self):
        self.alice = create_user()
        self.server = create_server()
        self.client.force_authenticate(self.alice)

    def test_create_returns_the_token_once(self):
        response = self.client.post("/api/v1/tokens/", {"mcp_server": self.server.pk, "name": "CLI"}, format="json")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["token"].startswith("itmcp_"))
        self.assertEqual((body["mcp"], body["state"]), ("mysql", "active"))
        self.assertEqual(response["Cache-Control"], "no-store")

        listed = self.client.get("/api/v1/tokens/").json()["results"]
        self.assertEqual(len(listed), 1)
        self.assertNotIn("token", listed[0])
        self.assertNotIn("token_hash", listed[0])

    def test_cannot_create_for_hidden_or_restricted_mcp(self):
        secret = restricted_server()
        response = self.client.post("/api/v1/tokens/", {"mcp_server": secret.pk, "name": "x"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["details"]["mcp_server"], ["MCP server not found."])

        grant(self.alice, secret, PermissionLevel.VIEW)
        response = self.client.post("/api/v1/tokens/", {"mcp_server": secret.pk, "name": "x"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AccessToken.objects.exists())

    def test_invalid_expiry(self):
        response = self.client.post(
            "/api/v1/tokens/", {"mcp_server": self.server.pk, "name": "x", "expires_in_days": 5000}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_list_shows_own_tokens_only(self):
        create_token(create_user("bob"), self.server)
        mine, _ = create_token(self.alice, self.server)
        self.assertEqual([t["id"] for t in self.client.get("/api/v1/tokens/").json()["results"]], [mine.pk])

    def test_delete_revokes(self):
        token, _ = create_token(self.alice, self.server)
        self.assertEqual(self.client.delete(f"/api/v1/tokens/{token.pk}/").status_code, 204)
        token.refresh_from_db()
        self.assertEqual(token.state, "revoked")

    def test_cannot_touch_someone_elses_token(self):
        token, _ = create_token(create_user("bob"), self.server)
        self.assertEqual(self.client.delete(f"/api/v1/tokens/{token.pk}/").status_code, 404)
        self.assertIsNone(AccessToken.objects.get().revoked_at)

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/v1/tokens/").status_code, 403)


class UserApiTests(APITestCase):
    def test_admins_only(self):
        create_service_account()
        self.client.force_authenticate(create_user())
        self.assertEqual(self.client.get("/api/v1/users/").status_code, 403)

        self.client.force_authenticate(create_user("boss", abilities=["manage_users"]))
        body = self.client.get("/api/v1/users/").json()
        by_name = {u["username"]: u for u in body["results"]}
        self.assertTrue(by_name["svc-ppc-server"]["is_service_account"])
        self.assertFalse(by_name["alice"]["is_service_account"])
        self.assertNotIn("password", by_name["alice"])


class McpManagePermissionApiTests(APITestCase):
    def setUp(self):
        self.alice = create_user()
        self.server = restricted_server()
        self.client.force_authenticate(self.alice)
        self.url = f"/api/v1/mcps/{self.server.pk}/"

    def test_even_manage_level_cannot_change_an_mcp_through_the_api(self):
        grant(self.alice, self.server, PermissionLevel.MANAGE)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.patch(self.url, {"description": "x"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(self.url).status_code, 403)


@override_settings(MCP_INTROSPECTION_KEY=KEY)
class IntrospectionApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.alice = create_user()
        self.server = create_server()
        self.token, self.plaintext = create_token(self.alice, self.server)

    def introspect(self, token=None, mcp="mysql", key=KEY):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {key}"} if key else {}
        return self.client.post(INTROSPECT, {"token": token or self.plaintext, "mcp": mcp}, format="json", **headers)

    def test_active_token(self):
        response = self.introspect()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            {k: body[k] for k in ("active", "mcp", "username", "token_id", "permission")},
            {"active": True, "mcp": "mysql", "username": "alice", "token_id": self.token.pk, "permission": "connect"},
        )
        self.assertEqual(body["expires_at"], int(self.token.expires_at.timestamp()))
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_inactive_answer_gives_no_reason(self):
        for kwargs in ({"mcp": "other"}, {"token": "itmcp_unknown"}):
            self.assertEqual(self.introspect(**kwargs).json(), {"active": False})

    def test_caller_must_present_the_key(self):
        self.assertEqual(self.introspect(key=None).status_code, 403)
        self.assertEqual(self.introspect(key="wrong" * 10).status_code, 403)

    def test_session_login_is_not_enough(self):
        self.client.force_authenticate(create_user("boss", abilities=["manage_users"]))
        self.assertEqual(self.introspect(key=None).status_code, 403)

    @override_settings(MCP_INTROSPECTION_KEY="")
    def test_disabled_when_no_key_configured(self):
        self.assertEqual(self.introspect().status_code, 403)

    def test_bad_request(self):
        response = self.client.post(INTROSPECT, {}, format="json", HTTP_AUTHORIZATION=f"Bearer {KEY}")
        self.assertEqual(response.status_code, 400)
