"""
MCPs added through code (mcp_servers/<slug>/mcp.json): discovery, start/stop, and their pages.

The runner tests start a real process: a tiny Python web server that answers
/health, standing in for an MCP.
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import textwrap
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import MCPActivityLog, MCPServer
from backend.services import catalog, runner

from .factories import create_user

FAKE_MCP = textwrap.dedent(
    """
    import os, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    print("fake MCP starting on port", os.environ["MCP_PORT"])
    print("secret visible:", "SHOULD_NOT_LEAK" in os.environ)
    if os.environ.get("MCP_SLUG") == "crashy":
        print("missing configuration"); sys.exit(3)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()
        def log_message(self, *args):
            pass

    HTTPServer(("127.0.0.1", int(os.environ["MCP_PORT"])), Handler).serve_forever()
    """
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FolderTestCase(TestCase):
    """Each test gets its own empty mcp_servers/ and log folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        override = override_settings(MCP_SERVERS_DIR=self.tmp / "mcp_servers", MCP_LOG_DIR=self.tmp / "logs")
        override.enable()
        self.addCleanup(override.disable)
        (self.tmp / "mcp_servers").mkdir()

    def add_mcp(self, slug: str, manifest: dict | str | None = None, *, files: dict | None = None) -> Path:
        folder = self.tmp / "mcp_servers" / slug
        folder.mkdir()
        if manifest is not None:
            text = manifest if isinstance(manifest, str) else json.dumps(manifest)
            (folder / "mcp.json").write_text(text, encoding="utf-8")
        for name, content in (files or {}).items():
            (folder / name).write_text(content, encoding="utf-8")
        return folder

    def fake_mcp(self, slug: str = "fake", **extra) -> MCPServer:
        manifest = {"name": f"{slug.title()} MCP", "command": [sys.executable, "server.py"], "port": free_port(), **extra}
        self.add_mcp(slug, manifest, files={"server.py": FAKE_MCP, "README.md": "# Fake\n\nUse **carefully**.\n"})
        catalog.sync()
        return MCPServer.objects.get(slug=slug)


class ManifestTests(FolderTestCase):
    def test_valid_manifest_with_defaults(self):
        folder = self.add_mcp("weather", {"name": "Weather", "command": ["uv", "run", "weather"], "port": 8120})
        manifest = catalog.load_manifest(folder)
        self.assertEqual(manifest.local_url, "http://127.0.0.1:8120/mcp")
        self.assertEqual(manifest.health_url, "http://127.0.0.1:8120/health")
        self.assertEqual(manifest.command, ("uv", "run", "weather"))

    def test_invalid_manifests_are_reported_not_crashing(self):
        self.add_mcp("broken-json", "{not json")
        self.add_mcp("no-command", {"name": "X", "port": 8130})
        self.add_mcp("bad-port", {"name": "X", "command": ["x"], "port": 80})
        self.add_mcp("extra-key", {"name": "X", "command": ["x"], "port": 8131, "password": "x"})
        self.add_mcp("Bad_Name", {"name": "X", "command": ["x"], "port": 8132})
        self.add_mcp("admin", {"name": "X", "command": ["x"], "port": 8133})
        self.add_mcp("no-manifest")  # folders without mcp.json are ignored
        manifests, errors = catalog.discover()
        self.assertEqual(manifests, [])
        self.assertEqual(len(errors), 6, errors)

    def test_duplicate_ports_are_rejected(self):
        self.add_mcp("first", {"name": "First", "command": ["x"], "port": 8140})
        self.add_mcp("second", {"name": "Second", "command": ["x"], "port": 8140})
        manifests, errors = catalog.discover()
        self.assertEqual([m.slug for m in manifests], ["first"])
        self.assertIn("already used by first", errors[0])

    def test_manual_outside_the_folder_is_ignored(self):
        folder = self.add_mcp("sneaky", {"name": "S", "command": ["x"], "port": 8150, "manual": "../../secret.md"})
        (self.tmp / "secret.md").write_text("secret", encoding="utf-8")
        self.assertIsNone(catalog.load_manifest(folder).manual_path)


class SyncTests(FolderTestCase):
    def test_sync_creates_restricted_stopped_entries_and_logs_them(self):
        self.add_mcp("weather", {"name": "Weather", "description": "Forecasts", "command": ["x"], "port": 8160})
        report = catalog.sync()
        self.assertEqual(report.created, ["weather"])
        server = MCPServer.objects.get(slug="weather")
        self.assertEqual(
            (server.name, server.description, server.deployment_type, server.access_policy, server.status, server.source_dir),
            ("Weather", "Forecasts", "local", "restricted", "stopped", "weather"),
        )
        self.assertEqual(server.internal_endpoint, "http://127.0.0.1:8160/mcp")
        self.assertTrue(MCPActivityLog.objects.filter(action="mcp_created", mcp_slug="weather").exists())
        self.assertEqual(catalog.sync().created, [])  # second run: nothing new

    def test_sync_updates_from_manifest_but_keeps_access_policy(self):
        folder = self.add_mcp("weather", {"name": "Weather", "command": ["x"], "port": 8161})
        catalog.sync()
        MCPServer.objects.filter(slug="weather").update(access_policy="all_users")
        (folder / "mcp.json").write_text(json.dumps({"name": "Weather v2", "command": ["x"], "port": 8162}), encoding="utf-8")
        self.assertEqual(catalog.sync().updated, ["weather"])
        server = MCPServer.objects.get(slug="weather")
        self.assertEqual((server.name, server.access_policy, server.internal_endpoint), ("Weather v2", "all_users", "http://127.0.0.1:8162/mcp"))

    def test_name_clash_is_reported(self):
        MCPServer.objects.create(name="Weather", slug="other", deployment_type="zip")
        self.add_mcp("weather", {"name": "Weather", "command": ["x"], "port": 8163})
        report = catalog.sync()
        self.assertEqual(report.created, [])
        self.assertTrue(report.errors)

    def test_rescan_button(self):
        self.client.force_login(create_user("admin", is_staff=True))
        self.add_mcp("weather", {"name": "Weather", "command": ["x"], "port": 8164})
        self.client.post(reverse("frontend:mcp-rescan"))
        self.assertTrue(MCPServer.objects.filter(slug="weather").exists())


class RunnerTests(FolderTestCase):
    def setUp(self):
        super().setUp()
        self.admin = create_user("admin", is_staff=True)
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for server in MCPServer.objects.filter(process_id__isnull=False):
            runner.stop(server, by=None)

    def test_start_check_and_stop(self):
        os.environ["SHOULD_NOT_LEAK"] = "1"
        self.addCleanup(os.environ.pop, "SHOULD_NOT_LEAK", None)
        server = self.fake_mcp()
        self.assertEqual(runner.get_state(server).state, "stopped")

        runner.start(server, by=self.admin)
        server.refresh_from_db()
        self.assertEqual(server.status, "running")
        state = runner.get_state(server)
        self.assertEqual(state.state, "running")
        self.assertTrue(state.healthy)

        output = runner.read_log(server)
        self.assertIn("started by admin", output)
        self.assertIn("fake MCP starting", output)
        self.assertIn("secret visible: False", output)  # the website's environment is not passed on

        with self.assertRaises(runner.RunnerError):
            runner.start(server, by=self.admin)  # already running

        pid = server.process_id
        runner.stop(server, by=self.admin)
        server.refresh_from_db()
        self.assertEqual((server.status, server.process_id), ("stopped", None))
        self.assertEqual(runner.get_state(server).state, "stopped")
        self.assertFalse(runner.psutil.pid_exists(pid) and runner.psutil.Process(pid).status() != "zombie")
        self.assertEqual(
            list(MCPActivityLog.objects.filter(mcp_slug="fake").order_by("id").values_list("action", flat=True)),
            ["mcp_created", "mcp_started", "mcp_stopped"],
        )

    def test_program_that_exits_at_once_is_reported(self):
        server = self.fake_mcp("crashy")
        with self.assertRaisesMessage(runner.RunnerError, "exit code 3"):
            runner.start(server, by=self.admin)
        server.refresh_from_db()
        self.assertEqual(server.status, "failed")
        self.assertIn("missing configuration", runner.read_log(server))
        self.assertEqual(runner.get_state(server).state, "crashed")

    def test_port_already_in_use(self):
        server = self.fake_mcp()
        port = catalog.manifest_for(server).port
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", port))
            blocker.listen()
            self.assertEqual(runner.get_state(server).state, "outside")
            with self.assertRaisesMessage(runner.RunnerError, "already in use"):
                runner.start(server, by=self.admin)
            with self.assertRaisesMessage(runner.RunnerError, "outside the platform"):
                runner.stop(server, by=self.admin)

    def test_missing_program(self):
        self.add_mcp("ghost", {"name": "Ghost", "command": ["no-such-program-xyz"], "port": free_port()})
        catalog.sync()
        with self.assertRaisesMessage(runner.RunnerError, "was not found"):
            runner.start(MCPServer.objects.get(slug="ghost"), by=self.admin)

    def test_recycled_process_id_is_not_ours(self):
        server = self.fake_mcp()
        MCPServer.objects.filter(pk=server.pk).update(process_id=os.getpid(), process_create_time=1.0, status="running")
        server.refresh_from_db()
        self.assertEqual(runner.get_state(server).state, "crashed")  # never treats the test process as the MCP

    def test_start_and_stop_buttons(self):
        server = self.fake_mcp()
        self.client.force_login(self.admin)
        detail = reverse("frontend:mcp-detail", args=[server.pk])
        self.assertContains(self.client.get(detail), reverse("frontend:mcp-control", args=[server.pk, "start"]))

        response = self.client.post(reverse("frontend:mcp-control", args=[server.pk, "start"]), {"next": detail})
        self.assertRedirects(response, detail)
        page = self.client.get(detail)
        self.assertContains(page, reverse("frontend:mcp-control", args=[server.pk, "stop"]))
        self.assertContains(self.client.get(reverse("frontend:mcp-log-output", args=[server.pk])), "fake MCP starting")
        logs = self.client.get(reverse("frontend:mcp-logs", args=[server.pk]))
        self.assertContains(logs, "Running")
        self.assertContains(logs, "by <strong>admin</strong>", html=False)
        download = self.client.get(reverse("frontend:mcp-log-download", args=[server.pk]))
        self.assertEqual(download["Content-Disposition"], 'attachment; filename="fake-output.log"')
        self.assertIn(b"started by admin", b"".join(download.streaming_content))

        self.client.post(reverse("frontend:mcp-control", args=[server.pk, "stop"]))
        server.refresh_from_db()
        self.assertEqual(server.status, "stopped")
        self.assertEqual(self.client.post(reverse("frontend:mcp-control", args=[server.pk, "explode"])).status_code, 403)


class LocalMcpPageTests(FolderTestCase):
    def setUp(self):
        super().setUp()
        self.admin = create_user("admin", is_staff=True)
        self.client.force_login(self.admin)

    def test_all_tabs(self):
        server = self.fake_mcp()
        for name in ("mcp-detail", "mcp-logs", "mcp-access", "mcp-manual", "mcp-settings"):
            response = self.client.get(reverse(f"frontend:{name}", args=[server.pk]))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, ">Manual</a>")
        self.assertContains(self.client.get(reverse("frontend:mcp-detail", args=[server.pk])), "127.0.0.1")

    def test_manual_is_rendered_and_html_is_escaped(self):
        server = self.fake_mcp()
        (self.tmp / "mcp_servers" / "fake" / "README.md").write_text(
            "# Fake\n\nUse **carefully**.\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n", encoding="utf-8"
        )
        response = self.client.get(reverse("frontend:mcp-manual", args=[server.pk]))
        self.assertContains(response, "<strong>carefully</strong>", html=False)
        self.assertNotContains(response, "<script>alert(1)</script>", html=False)
        self.assertNotContains(response, 'href="javascript:', html=False)
        self.assertContains(response, "claude mcp add --transport http fake")

    def test_local_mcp_with_folder_cannot_be_deleted(self):
        server = self.fake_mcp()
        self.assertNotContains(self.client.get(reverse("frontend:mcp-detail", args=[server.pk])), reverse("frontend:mcp-delete", args=[server.pk]))
        self.client.post(reverse("frontend:mcp-delete", args=[server.pk]))
        self.assertTrue(MCPServer.objects.filter(pk=server.pk).exists())

    def test_folder_removed_shows_warning_and_allows_removal(self):
        server = self.fake_mcp()
        shutil.rmtree(self.tmp / "mcp_servers" / "fake")
        response = self.client.get(reverse("frontend:mcp-detail", args=[server.pk]))
        self.assertContains(response, "Folder or mcp.json missing")
        self.client.post(reverse("frontend:mcp-delete", args=[server.pk]))
        self.assertFalse(MCPServer.objects.filter(pk=server.pk).exists())

    def test_state_partial(self):
        server = self.fake_mcp()
        response = self.client.get(reverse("frontend:mcp-state", args=[server.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Stopped")
        self.assertNotContains(response, "<html")


class LogFilterPageTests(FolderTestCase):
    """The Logs tab's severity and search filters, through the real pages."""

    def setUp(self):
        super().setUp()
        from .test_mcp_logs import LOG

        self.client.force_login(create_user("admin", is_staff=True))
        self.server = self.fake_mcp()
        path = runner.log_path(self.server)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LOG, encoding="utf-8")
        self.url = reverse("frontend:mcp-log-output", args=[self.server.pk])

    def test_search(self):
        response = self.client.get(self.url, {"q": "lost connection"})
        self.assertContains(response, "Query failed")
        self.assertNotContains(response, "Query took 9 s")

    def test_errors_only(self):
        response = self.client.get(self.url, {"severity": "errors"})
        self.assertContains(response, "Query failed")
        self.assertNotContains(response, "Query took 9 s")

    def test_old_problems_link_still_works_and_bad_values_are_ignored(self):
        self.assertNotContains(self.client.get(self.url, {"problems": "1"}), "Ready – accepting connections")
        self.assertContains(self.client.get(self.url, {"severity": "nonsense"}), "Ready – accepting connections")

    def test_no_match_message(self):
        self.assertContains(self.client.get(self.url, {"q": "zebra"}), "No events match “zebra”")

    def test_full_page_keeps_the_chosen_filters(self):
        page = self.client.get(reverse("frontend:mcp-logs", args=[self.server.pk]), {"severity": "errors", "q": "query"})
        self.assertContains(page, 'value="query"')
        self.assertContains(page, 'value="errors" checked')
