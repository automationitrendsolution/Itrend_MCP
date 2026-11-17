"""OAuth sign-in for MCP clients such as Claude in the browser (backend/services/oauth.py)."""

import base64
import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from backend.models import AccessToken, MCPPermission, OAuthGrant, PermissionLevel
from backend.services import gateway, tokens

from .factories import PASSWORD, create_user, grant, restricted_server
from .test_gateway import FakeMCP

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "http://testserver/mcp/mysql/"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class OAuthFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = create_user()
        self.server = restricted_server(slug="mysql", internal_endpoint="http://127.0.0.1:8101/mcp")
        grant(self.alice, self.server, PermissionLevel.CONNECT)
        gateway.set_client(httpx.Client(transport=httpx.MockTransport(FakeMCP())))
        self.addCleanup(gateway.set_client, None)

    # -- helpers ------------------------------------------------------------

    def register(self, **extra) -> dict:
        body = {"client_name": "Claude", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none", **extra}
        response = self.client.post("/oauth/register", json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def authorize_params(self, client_id: str, challenge: str, **extra) -> dict:
        return {
            "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": "xyz",
            "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE, **extra,
        }

    def get_code(self, client_id: str, challenge: str) -> str:
        self.client.login(username="alice", password=PASSWORD)
        page = self.client.get("/oauth/authorize", self.authorize_params(client_id, challenge))
        self.assertContains(page, "Allow Claude to use")
        response = self.client.post("/oauth/authorize", {**self.authorize_params(client_id, challenge), "decision": "allow"})
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["state"], ["xyz"])
        self.client.logout()
        return query["code"][0]

    def exchange(self, client_id: str, code: str, verifier: str, **extra):
        return self.client.post("/oauth/token", {
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "redirect_uri": REDIRECT, "code_verifier": verifier, "resource": RESOURCE, **extra,
        })

    def connect(self) -> tuple[dict, dict]:
        client = self.register()
        verifier, challenge = pkce()
        answer = self.exchange(client["client_id"], self.get_code(client["client_id"], challenge), verifier)
        self.assertEqual(answer.status_code, 200, answer.content)
        return client, answer.json()

    def call_mcp(self, access_token: str):
        return self.client.post("/mcp/mysql/", b"{}", content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {access_token}")

    # -- discovery ----------------------------------------------------------

    def test_gateway_401_points_to_the_sign_in_metadata(self):
        response = self.client.post("/mcp/mysql/", b"{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertIn('resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp/mysql/"',
                      response["WWW-Authenticate"])

    def test_metadata_documents(self):
        resource = self.client.get("/.well-known/oauth-protected-resource/mcp/mysql").json()
        self.assertEqual(resource["resource"], RESOURCE)
        self.assertEqual(resource["authorization_servers"], ["http://testserver"])
        server = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(server["token_endpoint"], "http://testserver/oauth/token/")
        self.assertEqual(server["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource/mcp/nope").status_code, 404)

    # -- the happy path -----------------------------------------------------

    def test_full_flow_gives_a_working_token_for_that_mcp(self):
        _, answer = self.connect()
        self.assertEqual(answer["token_type"], "Bearer")
        self.assertEqual(self.call_mcp(answer["access_token"]).status_code, 200)
        token = AccessToken.objects.get()
        self.assertEqual((token.user, token.mcp_server), (self.alice, self.server))
        self.assertIn("browser sign-in", token.name)

    def test_refresh_rotates_both_tokens(self):
        client, first = self.connect()
        response = self.client.post("/oauth/token", {"grant_type": "refresh_token", "client_id": client["client_id"],
                                                     "refresh_token": first["refresh_token"]})
        self.assertEqual(response.status_code, 200, response.content)
        second = response.json()
        self.assertEqual(self.call_mcp(first["access_token"]).status_code, 401)
        self.assertEqual(self.call_mcp(second["access_token"]).status_code, 200)
        reused = self.client.post("/oauth/token", {"grant_type": "refresh_token", "client_id": client["client_id"],
                                                   "refresh_token": first["refresh_token"]})
        self.assertEqual(reused.json()["error"], "invalid_grant")
        self.assertEqual(AccessToken.objects.count(), 1)

    def test_confidential_client_must_send_its_secret(self):
        client = self.register(token_endpoint_auth_method="client_secret_basic")
        verifier, challenge = pkce()
        code = self.get_code(client["client_id"], challenge)
        wrong = self.exchange(client["client_id"], code, verifier, client_secret="nope")
        self.assertEqual(wrong.status_code, 401)
        basic = base64.b64encode(f"{client['client_id']}:{client['client_secret']}".encode()).decode()
        right = self.client.post("/oauth/token", {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                                  "code_verifier": verifier}, HTTP_AUTHORIZATION=f"Basic {basic}")
        self.assertEqual(right.status_code, 200, right.content)

    # -- refusals -----------------------------------------------------------

    def test_code_needs_the_right_verifier_and_works_once(self):
        client = self.register()
        verifier, challenge = pkce()
        code = self.get_code(client["client_id"], challenge)
        self.assertEqual(self.exchange(client["client_id"], code, "x" * 50).json()["error"], "invalid_grant")
        self.assertEqual(self.exchange(client["client_id"], code, verifier).status_code, 200)
        self.assertEqual(self.exchange(client["client_id"], code, verifier).json()["error"], "invalid_grant")

    def test_people_without_connect_cannot_allow(self):
        create_user("bob")
        client = self.register()
        _, challenge = pkce()
        self.client.login(username="bob", password=PASSWORD)
        page = self.client.get("/oauth/authorize", self.authorize_params(client["client_id"], challenge))
        self.assertContains(page, "can't connect")
        response = self.client.post("/oauth/authorize", {**self.authorize_params(client["client_id"], challenge), "decision": "allow"})
        self.assertIn("error=access_denied", response["Location"])

    def test_deny_sends_access_denied(self):
        client = self.register()
        _, challenge = pkce()
        self.client.login(username="alice", password=PASSWORD)
        response = self.client.post("/oauth/authorize", {**self.authorize_params(client["client_id"], challenge), "decision": "deny"})
        self.assertTrue(response["Location"].startswith(REDIRECT + "?"))
        self.assertIn("error=access_denied", response["Location"])

    def test_unregistered_redirect_is_never_followed(self):
        client = self.register()
        _, challenge = pkce()
        self.client.login(username="alice", password=PASSWORD)
        response = self.client.get("/oauth/authorize", self.authorize_params(client["client_id"], challenge,
                                                                             redirect_uri="https://evil.example/cb"))
        self.assertEqual(response.status_code, 400)

    def test_not_signed_in_goes_to_the_login_page_and_back(self):
        client = self.register()
        _, challenge = pkce()
        response = self.client.get("/oauth/authorize", self.authorize_params(client["client_id"], challenge))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/?next=/oauth/authorize", response["Location"])

    def test_registration_rejects_unsafe_redirects(self):
        for uri in ("http://claude.ai/cb", "javascript:alert(1)", "https://x.example/cb#frag"):
            response = self.client.post("/oauth/register", json.dumps({"redirect_uris": [uri]}), content_type="application/json")
            self.assertEqual(response.status_code, 400, uri)

    def test_revoking_the_token_ends_the_connection(self):
        client, answer = self.connect()
        tokens.revoke_token(AccessToken.objects.get(), revoked_by=self.alice)
        self.assertEqual(self.call_mcp(answer["access_token"]).status_code, 401)
        response = self.client.post("/oauth/token", {"grant_type": "refresh_token", "client_id": client["client_id"],
                                                     "refresh_token": answer["refresh_token"]})
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_losing_access_stops_refresh(self):
        client, answer = self.connect()
        MCPPermission.objects.filter(user=self.alice).delete()
        response = self.client.post("/oauth/token", {"grant_type": "refresh_token", "client_id": client["client_id"],
                                                     "refresh_token": answer["refresh_token"]})
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_expired_refresh_token_is_refused(self):
        client, answer = self.connect()
        OAuthGrant.objects.update(refresh_expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.post("/oauth/token", {"grant_type": "refresh_token", "client_id": client["client_id"],
                                                     "refresh_token": answer["refresh_token"]})
        self.assertEqual(response.json()["error"], "invalid_grant")
