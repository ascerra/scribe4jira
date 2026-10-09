"""Deterministic security gates shared by pre/post stages."""

from __future__ import annotations

import re
from collections.abc import Iterable

SENSITIVE_PATTERNS = [
    re.compile(r"(ghp|gho|ghs|ghr)_[A-Za-z0-9_]{36,}", re.I),
    re.compile(r"\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN.*PRIVATE KEY", re.I),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b"),
    re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(
        r"(api[_-]?key|token|secret|password|bearer)[\s:=]+['\"]?[A-Za-z0-9_.~+/-]{20,}",
        re.I,
    ),
]

# Tag chars need 8-digit \U escapes; \uE0000 is misread and falsely matches ASCII.
SUSPICIOUS_UNICODE = re.compile(
    "[\U000e0000-\U000e007f\u200b-\u200d\ufeff\u202a-\u202e\u2066-\u2069]"
)

PUBLIC_SAFE_CATEGORIES = {
    None,
    "names",
    "interpersonal",
    "hr",
    "strategy",
    "security",
    "legal",
    "confidential",
}


def contains_sensitive(text: str) -> bool:
    """Check if text contains PII or sensitive patterns (emails, SSN, phone numbers).

        text: Text to check for sensitive content

    returns: True if sensitive patterns detected, False otherwise
    """
    return any(pattern.search(text or "") for pattern in SENSITIVE_PATTERNS)


def contains_suspicious_unicode(text: str) -> bool:
    """Check if text contains suspicious Unicode characters (zero-width, control chars).

        text: Text to check for suspicious characters

    returns: True if suspicious Unicode found, False otherwise
    """
    return bool(SUSPICIOUS_UNICODE.search(text or ""))


def contains_code_fence(text: str) -> bool:
    """Check if text contains markdown code fence (triple backticks).

        text: Text to check for code fences

    returns: True if code fence found, False otherwise
    """
    return "```" in (text or "")


def scrub_pii(text: str) -> str:
    """Apply the same redaction patterns used by the upstream pre-script.

        text: Text to scrub for PII

    returns: Text with PII patterns replaced with [REDACTED]
    """
    rules = [
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED]"),
        (r"\b(\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b", "[REDACTED]"),
        (r"\+\d{1,3}[-. ]?\d{4,14}\b", "[REDACTED]"),
        (r"\b([0-9]{1,3}\.){3}[0-9]{1,3}\b", "[REDACTED]"),
        (r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b", "[REDACTED]"),
        (r"\b([0-9][ -]?){13,19}\b", "[REDACTED]"),
        (r"\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b", "[REDACTED]"),
        (r"(ghp|gho|ghs|ghr)_[A-Za-z0-9_]{36,255}\b", "[REDACTED]"),
        (
            r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+",
            "[REDACTED]",
        ),
        (r"-----BEGIN[ \t]+(?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----.*", "[REDACTED]"),
        (
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
            "[REDACTED]",
        ),
        (
            r"(?i)(api[_-]?key|token|secret|password|bearer)[\s:=]+['\"]?[A-Za-z0-9_.~+/-]{20,}['\"]?",
            "[REDACTED]",
        ),
    ]
    cleaned = text or ""
    for pattern, replacement in rules:
        cleaned = re.sub(pattern, replacement, cleaned)
    return cleaned


def structural_scrub(raw_text: str) -> str:
    """Strip Gemini transcript sections and attendee attributions.

        raw_text: Raw meeting notes text

    returns: Cleaned text with metadata sections removed
    """
    lines = (raw_text or "").replace("\r", "").splitlines()
    kept: list[str] = []
    for line in lines:
        if line.startswith("Invited "):
            continue
        if line.startswith("Attendees:"):
            continue
        if line.strip() == "Participants:":
            continue
        if re.match(r"^(Organizer|Host|Co-host):?", line, re.I):
            kept.append("[meeting role line removed]")
            continue
        if line.strip() == "Details":
            break
        kept.append(line)
    text = "\n".join(kept)
    text = re.sub(r"\[[A-Z][a-zA-Z .,-]+\]", "[attendee]", text)
    return scrub_pii(text)


def strip_suspicious_unicode(text: str) -> str:
    """Remove suspicious Unicode characters from text.

        text: Text to clean

    returns: Text with suspicious Unicode characters removed
    """
    return SUSPICIOUS_UNICODE.sub("", text or "")


def dedupe_topics(topics: Iterable[dict]) -> list[dict]:
    """Merge multiple topics that reference the same existing Jira issue.

        topics: Iterable of topic dictionaries

    returns: Deduplicated list with merged summaries for same issue keys
    """
    merged: dict[str, dict] = {}
    passthrough: list[dict] = []
    for topic in topics:
        key = topic.get("existing_issue_id")
        if not key:
            passthrough.append(topic)
            continue
        if key not in merged:
            merged[key] = dict(topic)
            continue
        current = merged[key]
        current["summary"] = f"{current.get('summary', '')}\n\n{topic.get('summary', '')}".strip()
        current["confidence"] = max(current.get("confidence", 0), topic.get("confidence", 0))
        if topic.get("public_safe") is False:
            current["public_safe"] = False
            current["public_safe_category"] = topic.get("public_safe_category")
    return list(merged.values()) + passthrough
