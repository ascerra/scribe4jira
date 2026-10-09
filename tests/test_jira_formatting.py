"""Unit tests for Jira presentation helpers."""

from __future__ import annotations

import pytest

from scribe4jira.jira_formatting import (
    COMMENT_FOOTER_PREFIX,
    agent_banner_text,
    comment_footer_text,
    issue_browse_url,
    keys_to_linkify,
    linkify_issue_keys,
    story_point_suggestion_text,
)


def test_agent_banner_text_contains_attribution() -> None:
    """Agent banner text contains attribution."""
    text = agent_banner_text()

    assert "automatically generated from meeting notes" in text
    assert "Scribe agent" in text


def test_comment_footer_text_uses_exact_prefix() -> None:
    """Comment footer text uses exact prefix."""
    url = "https://docs.google.com/document/d/abc/edit"

    footer = comment_footer_text(url)

    assert footer == (f"{COMMENT_FOOTER_PREFIX}{url}")


def test_comment_footer_text_returns_empty_without_url() -> None:
    """Comment footer text returns empty without url."""
    assert comment_footer_text("") == ""


def test_story_point_suggestion_text_is_advisory() -> None:
    """Story point suggestion text is advisory."""
    text = story_point_suggestion_text(5)

    assert "Suggested story points" in text
    assert "5" in text


def test_issue_browse_url_builds_jira_link() -> None:
    """Issue browse url builds jira link."""
    url = issue_browse_url("https://jira.example.com/", "TEST-42")

    assert url == "https://jira.example.com/browse/TEST-42"


@pytest.mark.parametrize(
    ("input_text", "known_keys", "expected"),
    [
        pytest.param(
            "Builds on TEST-42",
            {"TEST-42"},
            "Builds on [TEST-42](https://jira.example.com/browse/TEST-42)",
            id="known_key_becomes_markdown_link",
        ),
        pytest.param(
            "See TEST-99 for context",
            {"TEST-42"},
            "See TEST-99 for context",
            id="unknown_key_stays_plain",
        ),
        pytest.param(
            "[TEST-42](https://jira.example.com/browse/TEST-42)",
            {"TEST-42"},
            "[TEST-42](https://jira.example.com/browse/TEST-42)",
            id="existing_markdown_link_unchanged",
        ),
        pytest.param(
            "[See TEST-42 docs](https://example.com) and TEST-43",
            {"TEST-42", "TEST-43"},
            "[See TEST-42 docs](https://example.com) and "
            "[TEST-43](https://jira.example.com/browse/TEST-43)",
            id="key_inside_link_label_unchanged",
        ),
        pytest.param(
            "Builds on TEST-42 and TEST-43",
            {"TEST-42", "TEST-43"},
            "Builds on [TEST-42](https://jira.example.com/browse/TEST-42) and "
            "[TEST-43](https://jira.example.com/browse/TEST-43)",
            id="multiple_known_keys_linkified",
        ),
        pytest.param(
            "See https://jira.example.com/browse/TEST-42 for details",
            {"TEST-42"},
            "See https://jira.example.com/browse/TEST-42 for details",
            id="bare_url_with_key_unchanged",
        ),
    ],
)
def test_linkify_issue_keys(input_text, known_keys, expected) -> None:
    """Linkify issue keys turns known plain keys into markdown links."""
    result = linkify_issue_keys(input_text, known_keys, "https://jira.example.com")

    assert result == expected


def test_linkify_issue_keys_is_idempotent() -> None:
    """Linkify issue keys is idempotent on already-linked text."""
    known = {"TEST-42"}
    server = "https://jira.example.com"
    text = "Builds on TEST-42"
    once = linkify_issue_keys(text, known, server)

    assert linkify_issue_keys(once, known, server) == once


def test_keys_to_linkify_matches_linkify_candidates() -> None:
    """Keys to linkify returns keys that linkify would convert."""
    text = "See TEST-42 and [See TEST-99 docs](https://example.com)"
    known = {"TEST-42", "TEST-99"}

    assert keys_to_linkify(text, known) == {"TEST-42"}
