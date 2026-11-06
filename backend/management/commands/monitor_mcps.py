"""
manage.py monitor_mcps – the monitor worker.

Runs next to the website as its own program:

    uv run python manage.py monitor_mcps          # keeps running (Ctrl+C to stop)
    uv run python manage.py monitor_mcps --once   # check every MCP once and exit

It is a separate program on purpose: health checks, measurements and
automatic restarts keep working while the website is busy, and a problem in
an MCP can never slow down or crash the website.
"""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from backend.services import monitoring

logger = logging.getLogger(__name__)

PURGE_EVERY_SECONDS = 3600


class Command(BaseCommand):
    help = "Watch the MCPs: real health checks, CPU/RAM, incidents and safe automatic restarts."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Check every MCP once, print the results and exit.")

    def handle(self, *args, once: bool = False, **options):
        sampler = monitoring.ResourceSampler()
        if once:
            monitoring.prime(sampler)
            time.sleep(1)  # so CPU use can be measured
            for check in monitoring.run_once(sampler, force=True):
                self.stdout.write(self._describe(check))
            self.stdout.write("Done.")
            return

        tick = max(settings.MONITOR_TICK_SECONDS, 1)
        self.stdout.write(f"Monitoring MCPs every {tick} s (each MCP at its own interval). Press Ctrl+C to stop.")
        last_purge = 0.0
        try:
            while True:
                close_old_connections()  # survive database restarts
                try:
                    for check in monitoring.run_once(sampler):
                        self.stdout.write(self._describe(check))
                    if time.monotonic() - last_purge > PURGE_EVERY_SECONDS:
                        removed = monitoring.purge_old_data()
                        last_purge = time.monotonic()
                        if removed:
                            self.stdout.write(f"Removed {removed} old monitoring rows.")
                except Exception:  # e.g. the database is briefly unavailable: try again next round
                    logger.exception("Monitoring round failed")
                time.sleep(tick)
        except KeyboardInterrupt:
            self.stdout.write("Monitor stopped.")

    @staticmethod
    def _describe(check) -> str:
        parts = [f"{check.checked_at:%H:%M:%S}", check.mcp_server.slug, check.get_status_display()]
        if check.response_time_ms is not None:
            parts.append(f"{check.response_time_ms:.0f} ms")
        if check.cpu_percent is not None:
            parts.append(f"CPU {check.cpu_percent:.1f}%")
        if check.memory_mb is not None:
            parts.append(f"RAM {check.memory_mb:.0f} MB")
        if check.error_message:
            parts.append(check.error_message)
        return "  ".join(parts)
