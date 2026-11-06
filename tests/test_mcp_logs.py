"""The readable MCP server log (backend/services/mcplogs.py)."""

from django.test import SimpleTestCase

from backend.services import mcplogs

LOG = """
=== 2026-11-06 15:17:10 started by rasvi: uv run mysql-mcp ===
2026-11-06 15:17:12,383 INFO mysql_mcp.server Starting MySQL MCP 'mysql' on http://127.0.0.1:8101/mcp
INFO:     Started server process [5608]
INFO:     Waiting for application startup.
2026-11-06 15:17:12,548 INFO mcp.server.streamable_http_manager StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8101 (Press CTRL+C to quit)
INFO:     127.0.0.1:64546 - "GET /health HTTP/1.1" 200 OK
INFO:     127.0.0.1:64547 - "GET /health HTTP/1.1" 200 OK
INFO:     127.0.0.1:64550 - "POST /mcp HTTP/1.1" 200 OK
INFO:     127.0.0.1:64551 - "POST /mcp HTTP/1.1" 401 Unauthorized
2026-11-06 15:17:15,000 WARNING mysql_mcp.server Query took 9 s
2026-11-06 15:17:16,000 ERROR mysql_mcp.server Query failed
Traceback (most recent call last):
  File "server.py", line 10, in run
pymysql.err.OperationalError: (2013, 'Lost connection')

=== 2026-11-06 15:17:19 stopped by rasvi ===

=== 2026-11-06 15:18:00 started by rasvi: uv run mysql-mcp ===
missing MYSQL_PASSWORD
=== 2026-11-06 15:18:02 exited with code 1 ===

=== 2026-11-06 15:19:00 started by alice: uv run mysql-mcp ===
INFO:     Uvicorn running on http://127.0.0.1:8101 (Press CTRL+C to quit)
"""


class ParseTests(SimpleTestCase):
    def setUp(self):
        self.first, self.crashed, self.running = mcplogs.parse(LOG)

    def test_one_run_per_start(self):
        self.assertEqual(self.first.started_by, "rasvi")
        self.assertEqual(self.first.ended_how, "Stopped by rasvi")
        self.assertEqual(self.first.duration, "9 s")
        self.assertTrue(self.running.is_open)
        self.assertEqual(self.running.started_by, "alice")

    def test_noise_is_hidden_and_messages_are_plain(self):
        messages = [e.message for e in self.first.events]
        self.assertEqual(messages[0], "Starting MySQL MCP 'mysql' on http://127.0.0.1:8101/mcp")
        self.assertEqual(messages[1], "Ready – accepting connections on http://127.0.0.1:8101")
        joined = " ".join(messages)
        for noise in ("Started server process", "Waiting for application", "session manager", "GET /health"):
            self.assertNotIn(noise, joined)

    def test_health_checks_and_requests_are_counted(self):
        self.assertEqual((self.first.health_ok, self.first.requests), (2, 2))
        self.assertIn("A client was refused: missing or invalid token (401)", [e.message for e in self.first.events])

    def test_levels_and_traceback(self):
        levels = {e.message: e.level for e in self.first.events}
        self.assertEqual(levels["Query took 9 s"], "warning")
        error = self.first.events[-1]
        self.assertEqual(error.level, "error")
        self.assertTrue(error.message.startswith("Query failed – pymysql.err.OperationalError"), error.message)
        self.assertIn("Traceback (most recent call last):", error.details_text)
        self.assertEqual((self.first.warnings, self.first.errors), (2, 1))

    def test_failed_start(self):
        self.assertEqual(self.crashed.ended_how, "Could not start – exited with code 1")
        self.assertEqual(self.crashed.ended_level, "error")
        self.assertEqual([e.message for e in self.crashed.events], ["missing MYSQL_PASSWORD"])

    def test_summarize_newest_first_and_problems_only(self):
        runs = mcplogs.summarize(mcplogs.parse(LOG), problems_only=True)
        self.assertEqual(runs[0].started_by, "alice")
        self.assertEqual(runs[0].events, [])
        self.assertTrue(all(e.level in {"warning", "error"} for e in runs[2].events))

    def test_output_before_any_start_note(self):
        (run,) = mcplogs.parse("hello\n")
        self.assertIsNone(run.started_at)
        self.assertEqual(run.events[0].message, "hello")
