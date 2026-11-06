"""The MCP gateway at /mcp/<slug>/ (backend/services/gateway.py)."""

import json
import sys

import httpx
from django.core.cache import cache
from django.test import TestCase, override_settings

from backend.models import MCPActivityLog, MCPServer, PermissionLevel
from backend.services import catalog, gateway, runner

from .factories import create_service_account, create_token, create_user, grant, restricted_server
from .test_local_mcps import FolderTestCase


class Streamed(httpx.SyncByteStream):
    """A response body that arrives as a stream, like from a real MCP (MockTransport pre-reads plain bytes)."""

    def __init__(self, body: bytes):
        self.body = body

    def __iter__(self):
        yield self.body


class FakeMCP:
    """Records what the gateway sent and answers like an MCP would."""

    def __init__(self, *, fail: Exception | None = None, sse: bool = False):
        self.requests: list[httpx.Request] = []
        self.fail, self.sse = fail, sse

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise self.fail
        if self.sse:
            body = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
            return httpx.Response(200, headers={"content-type": "text/event-stream", "mcp-session-id": "s1", "set-cookie": "x=1"}, stream=Streamed(body))
        headers = {"content-type": "application/json", "mcp-session-id": "s1", "server": "uvicorn"}
        return httpx.Response(200, headers=headers, stream=Streamed(b'{"ok": true}'))


class GatewayTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = create_user()
        self.server = restricted_server(slug="mysql", internal_endpoint="http://127.0.0.1:8101/mcp")
        grant(self.alice, self.server, PermissionLevel.CONNECT)
        self.token, self.plaintext = create_token(self.alice, self.server)
        self.use(FakeMCP())

    def use(self, fake: FakeMCP) -> FakeMCP:
        self.fake = fake
        gateway.set_client(httpx.Client(transport=httpx.MockTransport(fake)))
        self.addCleanup(gateway.set_client, None)
        return fake

    def call(self, path="/mcp/mysql/", token=None, method="post", **extra):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token or self.plaintext}", "HTTP_ACCEPT": "application/json, text/event-stream"}
        headers.update(extra)
        if method in {"get", "delete"}:
            return getattr(self.client, method)(path, **headers)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        return getattr(self.client, method)(path, body, content_type="application/json", **headers)

    def test_valid_token_is_forwarded(self):
        response = self.call()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(b"".join(response.streaming_content)), {"ok": True})
        self.assertEqual(response["mcp-session-id"], "s1")
        self.assertFalse(response.has_header("server"))
        sent = self.fake.requests[0]
        self.assertEqual(str(sent.url), "http://127.0.0.1:8101/mcp")
        self.assertEqual(sent.headers["x-itrend-user"], "alice")
        self.assertEqual(sent.headers["x-itrend-permission"], "connect")
        self.assertEqual(json.loads(sent.content)["method"], "initialize")

    def test_works_with_and_without_trailing_slash_and_subpaths(self):
        self.call("/mcp/mysql")
        self.call("/mcp/mysql/extra?x=1")
        self.assertEqual([str(r.url) for r in self.fake.requests], ["http://127.0.0.1:8101/mcp", "http://127.0.0.1:8101/mcp/extra?x=1"])

    def test_no_or_bad_token_is_refused_before_reaching_the_mcp(self):
        no_token = self.client.post("/mcp/mysql/", "{}", content_type="application/json")
        bad = self.call(token="itmcp_" + "x" * 43)
        not_bearer = self.call(HTTP_AUTHORIZATION="Basic abc")
        self.assertEqual([no_token.status_code, bad.status_code, not_bearer.status_code], [401, 401, 401])
        self.assertIn("Bearer", no_token["WWW-Authenticate"])
        self.assertEqual(bad.json()["error"]["code"], "invalid_token")
        self.assertEqual(self.fake.requests, [])

    def test_token_for_another_mcp_or_without_permission_is_refused(self):
        other = restricted_server("Other", "other", internal_endpoint="http://127.0.0.1:8102/mcp")
        self.assertEqual(self.call("/mcp/other/").status_code, 401)
        self.server.permissions.all().delete()
        response = self.call()
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("permission", response.json()["error"]["message"].lower())  # never says why
        self.assertTrue(MCPActivityLog.objects.filter(action="mcp_connection", result="denied").exists())
        self.assertEqual(self.fake.requests, [])
        del other

    def test_unknown_or_external_mcp_is_404(self):
        restricted_server("Ext", "ext", deployment_type=MCPServer.DeploymentType.EXTERNAL, endpoint="https://x.example.com/mcp")
        self.assertEqual(self.call("/mcp/nope/").status_code, 404)
        self.assertEqual(self.call("/mcp/ext/").status_code, 404)

    def test_identity_headers_cannot_be_forged_and_cookies_are_dropped(self):
        self.call(HTTP_X_ITREND_USER="admin", HTTP_X_ITREND_PERMISSION="admin", HTTP_COOKIE="sessionid=abc")
        sent = self.fake.requests[0]
        self.assertEqual((sent.headers["x-itrend-user"], sent.headers["x-itrend-permission"]), ("alice", "connect"))
        self.assertNotIn("cookie", sent.headers)
        self.assertEqual(sent.headers["x-forwarded-proto"], "http")  # the scheme as Django sees it

    def test_event_stream_is_passed_through(self):
        self.use(FakeMCP(sse=True))
        response = self.call()
        self.assertEqual(response["content-type"], "text/event-stream")
        self.assertIn(b"event: message", b"".join(response.streaming_content))
        self.assertFalse(response.has_header("set-cookie"))
        self.assertEqual(response["X-Accel-Buffering"], "no")

    def test_stopped_mcp_gives_503(self):
        self.use(FakeMCP(fail=httpx.ConnectError("refused")))
        response = self.call()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "mcp_not_running")

    def test_slow_mcp_gives_504(self):
        self.use(FakeMCP(fail=httpx.ReadTimeout("slow")))
        self.assertEqual(self.call().status_code, 504)

    def test_get_and_delete_are_forwarded_put_is_not(self):
        self.assertEqual(self.call(method="get").status_code, 200)
        self.assertEqual(self.call(method="delete").status_code, 200)
        self.assertEqual(self.call(method="put").status_code, 405)
        self.assertEqual([r.method for r in self.fake.requests], ["GET", "DELETE"])

    def test_service_account_tokens_work(self):
        service = create_service_account()
        grant(service, self.server, PermissionLevel.CONNECT)
        _, plaintext = create_token(service, self.server, created_by=create_user("boss", is_staff=True))
        self.assertEqual(self.call(token=plaintext).status_code, 200)
        self.assertEqual(self.fake.requests[0].headers["x-itrend-user"], "svc-ppc-server")

    @override_settings(MCP_PUBLIC_BASE_URL="https://mcp.itrendsolution.com/mcp")
    def test_connection_link_points_at_the_gateway(self):
        self.assertEqual(self.server.connection_url, "https://mcp.itrendsolution.com/mcp/mysql/")


class RealProcessTests(FolderTestCase):
    """The gateway in front of a real running process."""

    def setUp(self):
        super().setUp()
        cache.clear()
        gateway.set_client(None)
        self.addCleanup(gateway.set_client, None)
        self.alice = create_user()

    def test_request_reaches_a_running_mcp_and_fails_cleanly_when_stopped(self):
        echo = (
            "import os, json\n"
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def do_POST(self):\n"
            "        body = json.dumps({'user': self.headers.get('x-itrend-user'), 'path': self.path}).encode()\n"
            "        self.send_response(200); self.send_header('content-type', 'application/json')\n"
            "        self.send_header('content-length', str(len(body))); self.end_headers(); self.wfile.write(body)\n"
            "    def do_GET(self):\n"
            "        self.send_response(200); self.end_headers()\n"
            "    def log_message(self, *a): pass\n"
            "HTTPServer(('127.0.0.1', int(os.environ['MCP_PORT'])), H).serve_forever()\n"
        )
        from .test_local_mcps import free_port

        self.add_mcp("echo", {"name": "Echo", "command": [sys.executable, "echo.py"], "port": free_port()}, files={"echo.py": echo})
        catalog.sync()
        server = MCPServer.objects.get(slug="echo")
        grant(self.alice, server, PermissionLevel.CONNECT)
        _, plaintext = create_token(self.alice, server)
        auth = {"HTTP_AUTHORIZATION": f"Bearer {plaintext}"}

        runner.start(server, by=None)
        self.addCleanup(runner.stop, server, by=None)
        response = self.client.post("/mcp/echo/", "{}", content_type="application/json", **auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(b"".join(response.streaming_content)), {"user": "alice", "path": "/mcp"})

        runner.stop(server, by=None)
        self.assertEqual(self.client.post("/mcp/echo/", "{}", content_type="application/json", **auth).status_code, 503)
