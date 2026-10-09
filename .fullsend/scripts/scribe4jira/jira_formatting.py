"""Jira presentation helpers — theme colors and reusable body fragments.

Formatting tokens live here so application logic stays free of hardcoded
colors, panel types, and attribution copy.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass

ISSUE_KEY_RE = re.compile(r"(?<!/)\b([A-Z][A-Z0-9]+-\d+)\b")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")


def _theme_color(name: str, default: str) -> str:
    """Resolve a theme color from an environment variable with a default fallback."""
    return os.environ.get(name, default).strip() or default


THEME_COLOR_INFO = _theme_color("SCRIBE_THEME_COLOR_INFO", "#0052CC")
THEME_COLOR_WARNING = _theme_color("SCRIBE_THEME_COLOR_WARNING", "#FF991F")

AGENT_BANNER_LINES = (
    "This issue was automatically generated from meeting notes by the Scribe agent.",
    "Please review, edit, and add any missing context before prioritizing.",
)

COMMENT_FOOTER_PREFIX = (
    "This update was automatically processed based on the Gemini Notes from this session: "
)


@dataclass(frozen=True)
class JiraTheme:
    """Configurable presentation tokens for Jira ADF output."""

    color_info: str = THEME_COLOR_INFO
    color_warning: str = THEME_COLOR_WARNING
    panel_info: str = "info"
    panel_warning: str = "warning"


def agent_banner_text() -> str:
    """Plain-text banner copy (one line per sentence)."""
    return "\n".join(AGENT_BANNER_LINES)


def comment_footer_text(notes_url: str) -> str:
    """Attribution footer appended to comment bodies."""
    if not notes_url:
        return ""
    return f"{COMMENT_FOOTER_PREFIX}{notes_url}"


def story_point_suggestion_text(points: int | float | str) -> str:
    """Read-only story point advice shown at the end of new issue descriptions."""
    return f"Suggested story points: {points}"


def issue_browse_url(server: str, issue_key: str) -> str:
    """Build a Jira browse URL for the given issue key."""
    return f"{server.rstrip('/')}/browse/{issue_key}"


def _iter_linkifiable_segments(text: str) -> Iterator[str]:
    """Yield text spans outside existing markdown links."""
    if not text:
        return
    last = 0
    for match in MARKDOWN_LINK_RE.finditer(text):
        if match.start() > last:
            yield text[last : match.start()]
        last = match.end()
    if last < len(text):
        yield text[last:]


def _linkify_segment(segment: str, known_keys: set[str] | frozenset[str], server: str) -> str:
    """Replace plain issue keys with markdown links in a single text span.

    Called only on text *outside* existing ``[label](url)`` links. Keys not in
    *known_keys* are left unchanged.
    """

    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in known_keys:
            return key
        return f"[{key}]({issue_browse_url(server, key)})"

    return ISSUE_KEY_RE.sub(replacer, segment)


def keys_to_linkify(text: str, known_keys: set[str] | frozenset[str]) -> set[str]:
    """Return known issue keys that ``linkify_issue_keys`` would convert.

    For dry-run / debug logging only — does not modify text. ``post_scribe``
    uses this to print which keys would become links when no Jira write occurs.
    """
    if not text or not known_keys:
        return set()
    keys: set[str] = set()
    for segment in _iter_linkifiable_segments(text):
        keys.update(k for k in ISSUE_KEY_RE.findall(segment) if k in known_keys)
    return keys


def linkify_issue_keys(text: str, known_keys: set[str] | frozenset[str], server: str) -> str:
    """Turn known plain issue keys into markdown links labeled with the key.

    Example: ``Builds on PROJ-42`` → ``Builds on [PROJ-42](https://…/browse/PROJ-42)``

    Only keys present in *known_keys* (typically from backlog.json / closed-issues.json)
    are linked. Unknown keys stay plain text. Existing markdown links are preserved unchanged.
    """
    if not text or not known_keys or not server:
        return text

    parts: list[str] = []
    last = 0
    for match in MARKDOWN_LINK_RE.finditer(text):
        if match.start() > last:
            parts.append(_linkify_segment(text[last : match.start()], known_keys, server))
        parts.append(match.group(0))
        last = match.end()
    if last < len(text):
        parts.append(_linkify_segment(text[last:], known_keys, server))
    return "".join(parts) if parts else text
