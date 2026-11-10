"""Show an MCP's README (its user manual) as a web page."""

from pathlib import Path

from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt

MAX_MANUAL_BYTES = 256 * 1024

# "html": False means any raw HTML in the file is shown as text, never run, and
# markdown-it refuses javascript:, vbscript: and file: links, so a manual
# cannot inject scripts into the website.
_renderer = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")


def render_markdown(text: str, *, heading_offset: int = 1) -> SafeString:
    """
    Markdown to HTML. Headings move down `heading_offset` levels (# becomes <h2>), because
    the page around the manual already has its own <h1>.
    """
    tokens = _renderer.parse(text)
    for token in tokens:
        if token.type in {"heading_open", "heading_close"}:
            token.tag = f"h{min(int(token.tag[1]) + heading_offset, 6)}"
    return mark_safe(_renderer.renderer.render(tokens, _renderer.options, {}))  # noqa: S308 - raw HTML disabled


def render_markdown_file(path: Path) -> SafeString:
    with path.open("rb") as file:
        data = file.read(MAX_MANUAL_BYTES)
    return render_markdown(data.decode("utf-8", errors="replace"))
