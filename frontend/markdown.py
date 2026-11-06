"""Show an MCP's README (its user manual) as a web page."""

from pathlib import Path

from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt

MAX_MANUAL_BYTES = 256 * 1024

# "html": False means any raw HTML in the file is shown as text, never run, and
# markdown-it refuses javascript:, vbscript: and file: links, so a manual
# cannot inject scripts into the website.
_renderer = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")


def render_markdown(text: str) -> SafeString:
    return mark_safe(_renderer.render(text))  # noqa: S308 - output of a renderer with raw HTML disabled


def render_markdown_file(path: Path) -> SafeString:
    with path.open("rb") as file:
        data = file.read(MAX_MANUAL_BYTES)
    return render_markdown(data.decode("utf-8", errors="replace"))
