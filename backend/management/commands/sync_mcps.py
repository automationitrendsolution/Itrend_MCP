from django.core.management.base import BaseCommand

from backend.services import catalog


class Command(BaseCommand):
    help = "Register or update every MCP folder under mcp_servers/ that has an mcp.json file."

    def handle(self, *args, **options):
        report = catalog.sync()
        for slug in report.created:
            self.stdout.write(self.style.SUCCESS(f"added    {slug}"))
        for slug in report.updated:
            self.stdout.write(f"updated  {slug}")
        for error in report.errors:
            self.stderr.write(self.style.WARNING(f"problem  {error}"))
        if not (report.created or report.updated or report.errors):
            self.stdout.write("Everything is up to date.")
