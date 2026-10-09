"""Security and quality gates shared by post_scribe and summary_renderer."""

from __future__ import annotations

from scribe4jira.jira_formatting import agent_banner_text, story_point_suggestion_text
from scribe4jira.security import (
    contains_code_fence,
    contains_sensitive,
    contains_suspicious_unicode,
)

MAX_COMMENT_LEN = 2000
MAX_BODY_LEN = 15000
MAX_TITLE_LEN = 200

ISSUE_BANNER = agent_banner_text() + "\n\n"


def gate_reason(
    title: str,
    body: str,
    confidence: float,
    min_confidence: float,
    *,
    comment: bool,
    story_points_suggestion: int | float | str | None = None,
) -> str | None:
    """Check if content passes security and quality gates.

    title: Issue title or comment summary
    body: Issue description or comment body
    confidence: LLM confidence score (0-1)
    min_confidence: Minimum acceptable confidence threshold
    comment: True if checking comment, False if checking new issue
    story_points_suggestion: Optional advisory story points appended to new issue bodies

    returns: Rejection reason string if content should be rejected, None if passes
    """
    if confidence < min_confidence:
        return f"confidence {confidence} below threshold {min_confidence}"
    if contains_sensitive(title) or contains_sensitive(body):
        return "contains sensitive content (PII, secrets)"
    if contains_suspicious_unicode(title) or contains_suspicious_unicode(body):
        return "contains suspicious Unicode (potential prompt injection)"
    if comment and contains_code_fence(body):
        return "comment contains code block (unexpected in meeting summary)"
    if comment and len(body) > MAX_COMMENT_LEN:
        return f"summary length {len(body)} exceeds max {MAX_COMMENT_LEN}"
    if not comment and len(title) > MAX_TITLE_LEN:
        return f"summary length {len(title)} exceeds max {MAX_TITLE_LEN}"
    if not comment:
        banner_len = len(ISSUE_BANNER) if ISSUE_BANNER else 0
        story_len = 0
        if story_points_suggestion is not None and str(story_points_suggestion).strip():
            story_len = len(f"\n\n{story_point_suggestion_text(story_points_suggestion)}")
        if banner_len + len(body) + story_len > MAX_BODY_LEN:
            return f"description exceeds {MAX_BODY_LEN}"
    return None
