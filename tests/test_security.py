"""Unit tests for security gates and scrubbing."""

from __future__ import annotations

import pytest

from scribe4jira.security import (
    contains_code_fence,
    contains_sensitive,
    contains_suspicious_unicode,
    dedupe_topics,
    scrub_pii,
    strip_suspicious_unicode,
    structural_scrub,
)


@pytest.mark.parametrize(
    "text",
    [
        "Contact alice@example.com for details",
        "token=ghp_abcdefghijklmnopqrstuvwxyz1234567890ABCD",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_contains_sensitive_detects_known_patterns(text) -> None:
    """Contains sensitive detects known patterns."""
    assert contains_sensitive(text)


@pytest.mark.parametrize(
    "text",
    [
        "Discussed backlog grooming and sprint planning.",
        "Ship the feature after code review.",
    ],
)
def test_contains_sensitive_ignores_clean_text(text) -> None:
    """Contains sensitive ignores clean text."""
    assert not contains_sensitive(text)


def test_contains_suspicious_unicode_detects_zero_width_space() -> None:
    """Contains suspicious unicode detects zero width space."""
    assert contains_suspicious_unicode("hello\u200bworld")


def test_contains_code_fence_detects_markdown_fence() -> None:
    """Contains code fence detects markdown fence."""
    text = "Here is code:\n```python\nprint('x')\n```"

    assert contains_code_fence(text)


@pytest.mark.parametrize(
    ("raw_text", "forbidden_pattern"),
    [
        ("Reach me at alice@example.com.", "alice@example.com"),
        ("SSN 123-45-6789.", "123-45-6789"),
    ],
)
def test_scrub_pii_redacts_sensitive_data(raw_text, forbidden_pattern) -> None:
    """Scrub pii redacts sensitive data."""
    cleaned = scrub_pii(raw_text)
    assert forbidden_pattern not in cleaned
    assert "[REDACTED]" in cleaned


@pytest.mark.parametrize(
    ("raw_text", "expected_present", "expected_absent"),
    [
        ("Attendees: Alice\nNotes", "Notes", "Attendees:"),
        ("Participants:\nNotes", "Notes", "Participants:"),
        ("Host: Alice\nNotes", "[meeting role line removed]", "Host: Alice"),
        ("Notes\nDetails\nHidden", "Notes", "Hidden"),
    ],
)
def test_structural_scrub_scenarios(raw_text, expected_present, expected_absent) -> None:
    """Structural scrub scenarios."""
    cleaned = structural_scrub(raw_text)
    assert expected_present in cleaned
    assert expected_absent not in cleaned


def test_strip_suspicious_unicode_removes_hidden_chars() -> None:
    """Strip suspicious unicode removes hidden chars."""
    assert strip_suspicious_unicode("a\u200bb") == "ab"


def test_dedupe_topics_passes_through_topics_without_issue_key() -> None:
    """Dedupe topics passes through topics without issue key."""
    topics = [{"summary": "New work", "confidence": 0.7, "public_safe": True}]

    merged = dedupe_topics(topics)

    assert merged == topics


def test_dedupe_topics_merges_summaries_for_same_key() -> None:
    """Dedupe topics merges summaries for same key."""
    topics = [
        {
            "existing_issue_id": "TEST-1",
            "summary": "First",
            "confidence": 0.5,
            "public_safe": True,
        },
        {
            "existing_issue_id": "TEST-1",
            "summary": "Second",
            "confidence": 0.5,
            "public_safe": True,
        },
    ]

    merged = dedupe_topics(topics)

    assert merged[0]["summary"] == "First\n\nSecond"


def test_dedupe_topics_keeps_highest_confidence() -> None:
    """Dedupe topics keeps highest confidence."""
    topics = [
        {
            "existing_issue_id": "TEST-1",
            "summary": "First",
            "confidence": 0.5,
            "public_safe": True,
        },
        {
            "existing_issue_id": "TEST-1",
            "summary": "Second",
            "confidence": 0.9,
            "public_safe": True,
        },
    ]

    merged = dedupe_topics(topics)

    assert merged[0]["confidence"] == 0.9


def test_dedupe_topics_marks_merged_topic_unsafe_when_any_source_unsafe() -> None:
    """Dedupe topics marks merged topic unsafe when any source unsafe."""
    topics = [
        {
            "existing_issue_id": "TEST-1",
            "summary": "First",
            "confidence": 0.5,
            "public_safe": True,
        },
        {
            "existing_issue_id": "TEST-1",
            "summary": "Second",
            "confidence": 0.9,
            "public_safe": False,
            "public_safe_category": "hr",
        },
    ]

    merged = dedupe_topics(topics)

    assert merged[0]["public_safe"] is False
    assert merged[0]["public_safe_category"] == "hr"
