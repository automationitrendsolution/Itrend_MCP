"""
Phase 6 – monitoring: real MCP health checks, CPU/RAM, incidents, safe automatic
restarts, gateway traffic counts and limits, the Monitoring pages and the health API.

Process tests start real programs (a small web server that behaves like an MCP:
/health answers 200 and the MCP address asks for a Bearer token) and really
crash, restart and stop them.
"""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import timedelta
from unittest import mock

import httpx
import psutil
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from backend.models import (
    MCPActivityLog,
    MCPHealthCheck,
    MCPMonitorEvent,
    MCPMonitorSettings,
    MCPRequestStat,
    MCPServer,
    PermissionLevel,
)
from backend.services import catalog, gateway, monitoring, runner, traffic

from .factories import create_server, create_token, create_user, grant, restricted_server
from .test_gateway import FakeMCP
from .test_local_mcps import FolderTestCase, free_port

Kind = MCPMonitorEvent.Kind

MCP_LIKE_SERVER = textwrap.dedent(
    """
    import os
    from http.server import BaseHTTPRequestHandler, HTTPServer

    SLUG = os.environ["MCP_SLUG"]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if SLUG.startswith("broken"):  # running, but not answering like an MCP
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer error="invalid_token"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    HTTPServer(("127.0.0.1", int(os.environ["MCP_PORT"])), Handler).serve_forever()
    """
)


class FakeSampler:
    """Returns fixed CPU/RAM figures, for testing the limits."""

    def __init__(self, cpu=None, memory=None):
        self.cpu, self.memory = cpu, memory

    def sample(self, pid):
        return self.cpu, self.memory


def manifest(port: int = 8101, health_path: str | None = "/health") -> catalog.Manifest:
    return catalog.Manifest(
        slug="probe", folder=None, name="Probe", description="", command=("x",), port=port, health_path=health_path
    )


def wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runner.port_in_use(port):
            return
        time.sleep(0.1)
    raise AssertionError(f"Nothing started on port {port}")


# ---------------------------------------------------------------------------
# The real MCP check
# ---------------------------------------------------------------------------


class ProbeTests(TestCase):
    def client_for(self, handler) -> httpx.Client:
        self.requests: list[httpx.Request] = []

        def record(request):
            self.requests.append(request)
            return handler(request)

        return httpx.Client(transport=httpx.MockTransport(record))

    def test_mcp_asking_for_a_bearer_token_is_healthy(self):
        def handler(request):
            if request.url.path == "/health":
                return httpx.Response(200)
            return httpx.Response(401, headers={"www-authenticate": 'Bearer error="invalid_token"'})

        result = monitoring.probe(manifest(), client=self.client_for(handler))
        self.assertTrue(result.ok, result.error)
        self.assertIsNotNone(result.response_time_ms)
        sent = json.loads(self.requests[1].content)
        self.assertEqual(sent["method"], "initialize")  # a real MCP message, not just a ping
        self.assertIn("text/event-stream", self.requests[1].headers["accept"])

    def test_session_opened_by_the_check_is_closed_again(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, headers={"mcp-session-id": "abc", "content-type": "text/event-stream"}, content=b"data: {}\n\n")
            return httpx.Response(200)

        result = monitoring.probe(manifest(), client=self.client_for(handler))
        self.assertTrue(result.ok)
        delete = self.requests[-1]
        self.assertEqual((delete.method, delete.headers["mcp-session-id"]), ("DELETE", "abc"))

    def test_a_running_process_that_does_not_speak_mcp_is_unhealthy(self):
        # The port answers and /health is fine, but the MCP address does not answer like an MCP.
        def handler(request):
            return httpx.Response(200) if request.url.path == "/health" else httpx.Response(404)

        result = monitoring.probe(manifest(), client=self.client_for(handler))
        self.assertFalse(result.ok)
        self.assertIn("HTTP 404", result.error)

    def test_401_without_bearer_challenge_is_not_an_mcp_answer(self):
        result = monitoring.probe(manifest(health_path=None), client=self.client_for(lambda request: httpx.Response(401)))
        self.assertFalse(result.ok)

    def test_failing_health_address(self):
        result = monitoring.probe(manifest(), client=self.client_for(lambda request: httpx.Response(503)))
        self.assertFalse(result.ok)
        self.assertIn("health address answered HTTP 503", result.error)

    def test_nothing_listening_and_timeouts(self):
        def refuse(request):
            raise httpx.ConnectError("refused")

        def slow(request):
            raise httpx.ReadTimeout("slow")

        refused = monitoring.probe(manifest(port=8199), client=self.client_for(refuse))
        timed_out = monitoring.probe(manifest(), client=self.client_for(slow))
        self.assertFalse(refused.ok)
        self.assertIn("Nothing is answering on port 8199", refused.error)
        self.assertIsNone(refused.response_time_ms)
        self.assertIn("did not answer within", timed_out.error)

    def test_against_a_real_process(self):
        port = free_port()
        env = {**os.environ, "MCP_PORT": str(port), "MCP_SLUG": "real"}
        process = subprocess.Popen([sys.executable, "-c", MCP_LIKE_SERVER], env=env)
        self.addCleanup(process.wait, 5)
        self.addCleanup(process.kill)
        wait_for_port(port)
        self.assertTrue(monitoring.probe(manifest(port=port)).ok)


# ---------------------------------------------------------------------------
# Checks, incidents and automatic restarts (real processes)
# ---------------------------------------------------------------------------


class MonitorTestCase(FolderTestCase):
    def setUp(self):
        super().setUp()
        override = override_settings(MONITOR_HEARTBEAT_FILE=self.tmp / "monitor.heartbeat")
        override.enable()
        self.addCleanup(override.disable)

    def mcp(self, slug: str = "watched", **limits) -> MCPServer:
        manifest = {"name": f"{slug.title()} MCP", "command": [sys.executable, "server.py"], "port": free_port()}
        self.add_mcp(slug, manifest, files={"server.py": MCP_LIKE_SERVER})
        catalog.sync()
        server = MCPServer.objects.get(slug=slug)
        if limits:
            MCPMonitorSettings.objects.create(mcp_server=server, **limits)
        return server

    def start(self, server: MCPServer) -> MCPServer:
        runner.start(server, by=None)
        self.addCleanup(self.stop_quietly, server.pk)
        wait_for_port(catalog.manifest_for(server).port)
        return server

    @staticmethod
    def stop_quietly(pk: int) -> None:
        server = MCPServer.objects.filter(pk=pk).first()
        if server is not None:
            try:
                runner.stop(server, by=None)
            except runner.RunnerError:
                pass

    @staticmethod
    def crash(server: MCPServer) -> None:
        server.refresh_from_db()
        process = psutil.Process(server.process_id)
        process.kill()
        process.wait(5)

    @staticmethod
    def events(server: MCPServer, kind: str):
        return MCPMonitorEvent.objects.filter(mcp_server=server, kind=kind)


class CheckTests(MonitorTestCase):
    def test_running_mcp_is_healthy_with_cpu_and_memory(self):
        server = self.start(self.mcp())
        sampler = monitoring.ResourceSampler()
        first = monitoring.check_server(server, sampler)
        second = monitoring.check_server(server, sampler)
        self.assertEqual(first.status, MCPHealthCheck.Status.HEALTHY, first.error_message)
        self.assertIsNone(first.cpu_percent)  # CPU needs two samples; never invented
        self.assertGreater(first.memory_mb, 1)
        self.assertIsNotNone(second.cpu_percent)
        self.assertGreaterEqual(second.cpu_percent, 0)
        self.assertFalse(MCPMonitorEvent.objects.exists())  # nothing changed, nothing to report

    def test_stopped_mcp_is_not_checked(self):
        server = self.mcp()
        self.assertIsNone(monitoring.check_server(server, monitoring.ResourceSampler()))
        self.assertFalse(MCPHealthCheck.objects.exists())

    def test_crash_is_reported_once_and_restarted(self):
        server = self.start(self.mcp())
        sampler = monitoring.ResourceSampler()
        monitoring.check_server(server, sampler)
        old_pid = MCPServer.objects.get(pk=server.pk).process_id
        self.crash(server)

        check = monitoring.check_server(server, sampler)
        self.assertEqual(check.status, MCPHealthCheck.Status.DOWN)
        self.assertEqual(self.events(server, Kind.WENT_DOWN).count(), 1)
        self.assertEqual(self.events(server, Kind.AUTO_RESTARTED).count(), 1)
        server.refresh_from_db()
        self.assertEqual(server.status, MCPServer.Status.RUNNING)
        self.assertNotEqual(server.process_id, old_pid)

        after = monitoring.check_server(server, sampler)
        self.assertEqual(after.status, MCPHealthCheck.Status.HEALTHY)
        self.assertEqual(self.events(server, Kind.RECOVERED).count(), 1)
        # The restart is in the activity log too, done by "the platform".
        started = MCPActivityLog.objects.filter(action="mcp_started", mcp_server=server).first()
        self.assertEqual(started.username, "")

    def test_no_restart_when_switched_off(self):
        server = self.start(self.mcp(auto_restart=False))
        self.crash(server)
        monitoring.check_server(server, monitoring.ResourceSampler())
        monitoring.check_server(server, monitoring.ResourceSampler())
        server.refresh_from_db()
        self.assertEqual(server.status, MCPServer.Status.FAILED)
        self.assertEqual(self.events(server, Kind.WENT_DOWN).count(), 1)  # reported once, not every check
        self.assertFalse(self.events(server, Kind.AUTO_RESTARTED).exists())

    def test_mcp_stopped_by_an_administrator_is_never_restarted(self):
        server = self.start(self.mcp())
        runner.stop(MCPServer.objects.get(pk=server.pk), by=None)
        self.assertIsNone(monitoring.check_server(server, monitoring.ResourceSampler()))
        server.refresh_from_db()
        self.assertEqual(server.status, MCPServer.Status.STOPPED)

    def test_restarts_pause_after_the_hourly_limit(self):
        server = self.start(self.mcp(max_restarts_per_hour=2))
        for _ in range(2):
            MCPMonitorEvent.objects.create(mcp_server=server, kind=Kind.AUTO_RESTARTED, message="earlier")
        self.crash(server)
        monitoring.check_server(server, monitoring.ResourceSampler())
        monitoring.check_server(server, monitoring.ResourceSampler())
        self.assertEqual(self.events(server, Kind.GAVE_UP).count(), 1)  # one alert, not one per check
        self.assertEqual(self.events(server, Kind.AUTO_RESTARTED).count(), 2)
        server.refresh_from_db()
        self.assertNotEqual(server.status, MCPServer.Status.RUNNING)

    def test_mcp_that_stops_answering_is_restarted_after_the_set_number_of_checks(self):
        server = self.start(self.mcp("broken", restart_after_failed_checks=2))
        sampler = monitoring.ResourceSampler()
        first = monitoring.check_server(server, sampler)
        self.assertEqual(first.status, MCPHealthCheck.Status.UNHEALTHY)
        self.assertIn("HTTP 500", first.error_message)
        self.assertEqual(self.events(server, Kind.UNHEALTHY).count(), 1)
        self.assertFalse(self.events(server, Kind.AUTO_RESTARTED).exists())

        monitoring.check_server(server, sampler)  # second failure in a row: restart
        self.assertEqual(self.events(server, Kind.AUTO_RESTARTED).count(), 1)

        monitoring.check_server(server, sampler)  # failures before the restart do not count again
        self.assertEqual(self.events(server, Kind.AUTO_RESTARTED).count(), 1)
        self.assertEqual(self.events(server, Kind.UNHEALTHY).count(), 1)

    def test_mcp_started_outside_the_platform_is_watched_but_never_touched(self):
        server = self.mcp("broken-outside", restart_after_failed_checks=1)
        port = catalog.manifest_for(server).port
        env = {**os.environ, "MCP_PORT": str(port), "MCP_SLUG": "broken-outside"}
        process = subprocess.Popen([sys.executable, "-c", MCP_LIKE_SERVER], env=env)
        self.addCleanup(process.wait, 5)
        self.addCleanup(process.kill)
        wait_for_port(port)

        check = monitoring.check_server(server, monitoring.ResourceSampler())
        self.assertEqual(check.status, MCPHealthCheck.Status.UNHEALTHY)
        self.assertIsNone(check.memory_mb)  # not our process: not measured
        self.assertFalse(self.events(server, Kind.AUTO_RESTARTED).exists())
        self.assertIsNone(process.poll())

    def test_memory_limit_restarts_after_two_checks_over_it(self):
        server = self.start(self.mcp(memory_limit_mb=100))
        monitoring.check_server(server, FakeSampler(memory=150.0))
        self.assertFalse(self.events(server, Kind.MEMORY_LIMIT).exists())  # one high reading is not enough
        monitoring.check_server(server, FakeSampler(memory=150.0))
        self.assertEqual(self.events(server, Kind.MEMORY_LIMIT).count(), 1)
        self.assertEqual(self.events(server, Kind.AUTO_RESTARTED).count(), 1)

    def test_cpu_limit_warns_once_when_crossed(self):
        server = self.start(self.mcp(cpu_limit_percent=50))
        for _ in range(3):
            monitoring.check_server(server, FakeSampler(cpu=90.0, memory=20.0))
        monitoring.check_server(server, FakeSampler(cpu=10.0, memory=20.0))
        monitoring.check_server(server, FakeSampler(cpu=95.0, memory=20.0))
        self.assertEqual(self.events(server, Kind.CPU_LIMIT).count(), 2)
        self.assertFalse(self.events(server, Kind.AUTO_RESTARTED).exists())  # CPU only warns


class WorkerTests(MonitorTestCase):
    def test_run_once_respects_each_interval(self):
        server = self.start(self.mcp(health_check_interval_seconds=3600))
        sampler = monitoring.ResourceSampler()
        self.assertEqual(len(monitoring.run_once(sampler)), 1)
        self.assertEqual(len(monitoring.run_once(sampler)), 0)  # not due again for an hour
        self.assertEqual(len(monitoring.run_once(sampler, force=True)), 1)
        self.assertEqual(MCPHealthCheck.objects.filter(mcp_server=server).count(), 2)

    def test_one_failing_mcp_does_not_stop_the_others(self):
        first, second = self.mcp("first"), self.mcp("second")
        self.start(first), self.start(second)
        real = monitoring.check_server

        def explode_on_first(server, sampler, **kwargs):
            if server.slug == "first":
                raise RuntimeError("boom")
            return real(server, sampler, **kwargs)

        with mock.patch.object(monitoring, "check_server", side_effect=explode_on_first), self.assertLogs("backend", "ERROR"):
            checks = monitoring.run_once(monitoring.ResourceSampler(), force=True)
        self.assertEqual([c.mcp_server.slug for c in checks], ["second"])

    def test_heartbeat_tells_the_website_the_worker_runs(self):
        self.assertFalse(monitoring.worker_is_running())
        monitoring.run_once(monitoring.ResourceSampler())
        self.assertTrue(monitoring.worker_is_running())

    def test_command_once(self):
        self.start(self.mcp())
        out = io.StringIO()
        call_command("monitor_mcps", "--once", stdout=out)
        self.assertIn("watched  Healthy", out.getvalue())
        self.assertIn("RAM", out.getvalue())

    def test_old_data_is_removed(self):
        server = create_server(slug="old")
        old = timezone.now() - timedelta(days=30)
        check = MCPHealthCheck.objects.create(mcp_server=server, status="healthy")
        MCPHealthCheck.objects.filter(pk=check.pk).update(checked_at=old)
        MCPHealthCheck.objects.create(mcp_server=server, status="healthy")
        MCPRequestStat.objects.create(mcp_server=server, bucket_start=old, request_count=1)
        event = MCPMonitorEvent.objects.create(mcp_server=server, kind=Kind.RECOVERED, message="x")
        MCPMonitorEvent.objects.filter(pk=event.pk).update(created_at=old)
        self.assertEqual(monitoring.purge_old_data(days=14), 3)
        self.assertEqual(MCPHealthCheck.objects.count(), 1)


# ---------------------------------------------------------------------------
# Gateway traffic and limits
# ---------------------------------------------------------------------------


class TrafficCountTests(TestCase):
    def setUp(self):
        self.server = create_server(slug="mysql")

    def test_answers_are_counted_in_five_minute_buckets(self):
        now = timezone.now().replace(minute=7, second=30)
        traffic.record(self.server, 200, 40, now=now)
        traffic.record(self.server, 200, 120, now=now + timedelta(minutes=1))
        traffic.record(self.server, 401, 5, now=now)
        traffic.record(self.server, 503, 10, now=now)
        traffic.record(self.server, 429, 1, now=now)
        traffic.record(self.server, 200, 30, now=now + timedelta(minutes=5))  # next bucket
        self.assertEqual(MCPRequestStat.objects.count(), 2)
        stat = MCPRequestStat.objects.order_by("bucket_start").first()
        self.assertEqual(stat.bucket_start.minute, 5)
        self.assertEqual((stat.request_count, stat.error_count, stat.denied_count), (5, 2, 1))
        self.assertEqual(stat.max_response_ms, 120)
        totals = traffic.totals(self.server)
        self.assertEqual((totals["requests"], totals["errors"], totals["denied"]), (6, 2, 1))
        self.assertEqual(totals["max_response_ms"], 120)

    def test_no_traffic_means_no_average(self):
        self.assertEqual(traffic.totals(self.server), {"requests": 0, "errors": 0, "denied": 0, "avg_response_ms": None, "max_response_ms": None})

    def test_counting_problems_never_break_a_request(self):
        from django.db import DatabaseError

        with mock.patch.object(MCPRequestStat.objects, "filter", side_effect=DatabaseError("down")), self.assertLogs("backend", "ERROR"):
            traffic.record(self.server, 200, 1)


class GatewayTrafficTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = create_user()
        self.server = restricted_server(slug="mysql", internal_endpoint="http://127.0.0.1:8101/mcp")
        grant(self.alice, self.server, PermissionLevel.CONNECT)
        _, self.plaintext = create_token(self.alice, self.server)
        self.fake = FakeMCP()
        gateway.set_client(httpx.Client(transport=httpx.MockTransport(self.fake)))
        self.addCleanup(gateway.set_client, None)

    def call(self, slug="mysql", token=None):
        return self.client.post(
            f"/mcp/{slug}/", "{}", content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token or self.plaintext}", HTTP_ACCEPT="application/json, text/event-stream",
        )

    def stat(self) -> MCPRequestStat:
        return MCPRequestStat.objects.get(mcp_server=self.server)

    def test_requests_refusals_and_failures_are_counted(self):
        b"".join(self.call().streaming_content)
        self.call(token="itmcp_" + "x" * 43)
        self.fake.fail = httpx.ConnectError("down")
        self.assertEqual(self.call().status_code, 503)
        self.call(slug="nope")  # unknown MCPs are not counted anywhere
        stat = self.stat()
        self.assertEqual((stat.request_count, stat.denied_count, stat.error_count), (3, 1, 1))

    def test_request_timeout_comes_from_the_mcp_settings(self):
        MCPMonitorSettings.objects.create(mcp_server=self.server, request_timeout_seconds=42)
        b"".join(self.call().streaming_content)
        self.assertEqual(self.fake.requests[0].extensions["timeout"]["read"], 42)

    def test_default_timeout_is_unchanged(self):
        b"".join(self.call().streaming_content)
        self.assertEqual(self.fake.requests[0].extensions["timeout"]["read"], gateway.TIMEOUT.read)

    def test_too_many_requests_at_once_get_busy(self):
        limits = MCPMonitorSettings.objects.create(mcp_server=self.server, max_concurrent_requests=1)
        held = traffic.acquire(self.server, limits)
        response = self.call()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "mcp_busy")
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.stat().error_count, 1)
        held.release()
        self.assertEqual(self.call().status_code, 200)

    def test_slot_is_freed_when_the_answer_ends_or_is_never_read(self):
        read = self.call()
        self.assertEqual(traffic.in_progress(self.server), 1)  # still streaming
        b"".join(read.streaming_content)
        self.assertEqual(traffic.in_progress(self.server), 0)

        # Django calls close() on the body when a client goes away without reading it.
        closed = []
        unread = traffic.ReleasingStream(mock.Mock(close=lambda: closed.append(True)), traffic.acquire(self.server, traffic.limits_for(self.server)))
        self.assertEqual(traffic.in_progress(self.server), 1)
        unread.close()
        unread.close()  # twice is harmless
        self.assertEqual(traffic.in_progress(self.server), 0)
        self.assertEqual(closed, [True, True])

    def test_slot_is_freed_when_the_mcp_is_down(self):
        self.fake.fail = httpx.ConnectError("down")
        self.call()
        self.assertEqual(traffic.in_progress(self.server), 0)


# ---------------------------------------------------------------------------
# Pages and API
# ---------------------------------------------------------------------------


# A monitor really running on this computer must not change what the pages show in tests.
@override_settings(MCP_DISCOVERY_INTERVAL=0, MONITOR_HEARTBEAT_FILE=os.path.join(tempfile.gettempdir(), "itmcp-test-no-monitor.heartbeat"))
class MonitoringPageTests(TestCase):
    def setUp(self):
        self.admin = create_user("boss", is_staff=True)
        self.alice = create_user()
        self.server = create_server(slug="mysql", deployment_type=MCPServer.DeploymentType.LOCAL, status=MCPServer.Status.RUNNING)
        MCPHealthCheck.objects.create(mcp_server=self.server, status="unhealthy", response_time_ms=12, cpu_percent=3.5, memory_mb=80, error_message="It did not answer within 5 seconds.")
        MCPMonitorEvent.objects.create(mcp_server=self.server, kind=Kind.UNHEALTHY, level="warning", message="MySQL MCP is running but not answering properly")
        traffic.record(self.server, 200, 50)
        traffic.record(self.server, 503, 10)

    def test_only_administrators_see_monitoring(self):
        url = reverse("frontend:monitoring")
        self.assertEqual(self.client.get(url).status_code, 302)  # to the login page
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(reverse("frontend:monitoring-detail", args=[self.server.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("frontend:dashboard")), reverse("frontend:monitoring"))

    def test_overview_shows_real_figures(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("frontend:monitoring"))
        self.assertContains(response, "Not answering")
        self.assertContains(response, "80 MB")
        self.assertContains(response, "not answering properly")
        self.assertContains(response, "The monitor is not running")
        self.assertEqual(response.context["traffic"]["requests"], 2)
        self.assertEqual(response.context["counts"], {"monitored": 1, "healthy": 0, "problems": 1})
        partial = self.client.get(reverse("frontend:monitoring"), HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(partial, "monitoring/partials/overview_live.html")
        self.assertTemplateNotUsed(partial, "base.html")

    def test_a_stopped_mcp_does_not_show_its_old_check(self):
        self.server.status = MCPServer.Status.STOPPED
        self.server.save()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("frontend:monitoring"))
        self.assertNotContains(response, "80 MB")
        self.assertEqual(response.context["rows"][0]["health"], "stopped")

    def test_detail_and_settings(self):
        self.client.force_login(self.admin)
        url = reverse("frontend:monitoring-detail", args=[self.server.pk])
        response = self.client.get(url)
        self.assertContains(response, "It did not answer within 5 seconds.")
        self.assertContains(response, "Limits and automatic restarts")
        self.assertEqual(response.context["hours"][0]["requests"], 2)

        data = {
            "health_check_interval_seconds": 60, "request_timeout_seconds": 120, "max_concurrent_requests": 5,
            "memory_limit_mb": 512, "cpu_limit_percent": "", "auto_restart": "on",
            "restart_after_failed_checks": 3, "max_restarts_per_hour": 3,
        }
        self.assertRedirects(self.client.post(url, data), url)
        saved = MCPMonitorSettings.objects.get(mcp_server=self.server)
        self.assertEqual((saved.memory_limit_mb, saved.max_concurrent_requests, saved.updated_by), (512, 5, self.admin))
        entry = MCPActivityLog.objects.get(action="mcp_updated")
        self.assertIn("memory_limit_mb", entry.metadata["changed_fields"])

        bad = self.client.post(url, {**data, "health_check_interval_seconds": 1})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "is-invalid")
        self.assertEqual(MCPMonitorSettings.objects.get(mcp_server=self.server).health_check_interval_seconds, 60)

    def test_sidebar_links_to_monitoring_for_administrators(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("frontend:dashboard")), reverse("frontend:monitoring"))


class HealthApiTests(TestCase):
    def setUp(self):
        self.admin = create_user("boss", is_staff=True)
        self.alice = create_user()
        self.server = create_server(slug="mysql", deployment_type=MCPServer.DeploymentType.LOCAL)
        self.hidden = restricted_server()
        MCPHealthCheck.objects.create(mcp_server=self.server, status="healthy", response_time_ms=8, cpu_percent=1.2, memory_mb=64, error_message="")
        traffic.record(self.server, 200, 20)

    def url(self, server) -> str:
        return reverse("backend:mcp-health", args=[server.pk])

    def test_login_required(self):
        self.assertEqual(self.client.get(self.url(self.server)).status_code, 403)

    def test_user_sees_health_but_not_internal_figures(self):
        self.client.force_login(self.alice)
        body = self.client.get(self.url(self.server)).json()
        self.assertEqual(body["last_check"]["status"], "healthy")
        self.assertNotIn("memory_mb", body["last_check"])
        self.assertNotIn("error", body["last_check"])
        self.assertEqual(body["last_24h"]["requests"], 1)
        self.assertEqual(body["last_24h"]["uptime_percent"], 100.0)

    def test_administrator_sees_resources(self):
        self.client.force_login(self.admin)
        body = self.client.get(self.url(self.server)).json()
        self.assertEqual(body["last_check"]["memory_mb"], 64)

    def test_hidden_mcp_is_404(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(self.url(self.hidden)).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/mcps/999999/health/").status_code, 404)

    def test_no_checks_yet(self):
        self.client.force_login(self.admin)
        body = self.client.get(self.url(self.hidden)).json()
        self.assertIsNone(body["last_check"])
        self.assertIsNone(body["last_24h"]["uptime_percent"])
