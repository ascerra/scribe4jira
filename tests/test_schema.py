"""Schema contract tests."""

from __future__ import annotations

import json

import jsonschema
import pytest
from conftest import SCRIBE_RESULT_SCHEMA_PATH


@pytest.fixture
def schema() -> dict:
    """Load the scribe-result JSON schema from disk."""
    return json.loads(SCRIBE_RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))


def test_sample_agent_result_validates_against_schema(schema, agent_result_payload) -> None:
    """Sample agent result validates against schema."""
    jsonschema.validate(agent_result_payload, schema)


def test_schema_rejects_missing_stats(schema) -> None:
    """Schema rejects missing stats."""
    payload: dict[str, list] = {"topics": [], "new_issues": []}

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


@pytest.mark.parametrize(
    "invalid_key",
    ["bad-key", "test-1", "123-ABC"],
)
def test_schema_rejects_invalid_jira_key(schema, invalid_key) -> None:
    """Schema rejects invalid jira key."""
    payload = {
        "topics": [
            {
                "topic": "Update",
                "summary": "Summary text",
                "existing_issue_id": invalid_key,
                "confidence": 0.5,
                "public_safe": True,
            }
        ],
        "new_issues": [],
        "stats": {
            "notes_processed": 1,
            "topics_extracted": 1,
            "existing_matched": 1,
            "new_proposed": 0,
            "omitted": 0,
        },
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


def _valid_new_issue(**overrides) -> dict:
    """Return a minimal valid new_issue entry, with optional overrides."""
    base = {
        "project_id": "TEST",
        "summary": "A task",
        "description": "Details",
        "issuetype": "Task",
        "priority": "Normal",
        "confidence": 0.8,
        "public_safe": True,
    }
    base.update(overrides)
    return base


def _wrap_new_issue(issue: dict) -> dict:
    """Wrap a new_issue in a valid top-level result payload."""
    return {
        "topics": [],
        "new_issues": [issue],
        "stats": {
            "notes_processed": 1,
            "topics_extracted": 0,
            "existing_matched": 0,
            "new_proposed": 1,
            "omitted": 0,
        },
    }


_SENTINEL = object()


@pytest.mark.parametrize(
    "parent_value",
    [
        pytest.param("TEST-42", id="valid_key"),
        pytest.param(None, id="null"),
        pytest.param(_SENTINEL, id="omitted"),
    ],
)
def test_schema_accepts_valid_parent_issue_id(schema, parent_value) -> None:
    """Schema accepts valid parent_issue_id values (key, null, or omitted)."""
    issue = (
        _valid_new_issue()
        if parent_value is _SENTINEL
        else _valid_new_issue(parent_issue_id=parent_value)
    )
    payload = _wrap_new_issue(issue)

    jsonschema.validate(payload, schema)


@pytest.mark.parametrize("bad_key", ["bad-key", "123-TEST", ""])
def test_schema_rejects_invalid_parent_issue_id(schema, bad_key) -> None:
    """Schema rejects parent_issue_id values that don't match the jira_key pattern."""
    payload = _wrap_new_issue(_valid_new_issue(parent_issue_id=bad_key))

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_schema_rejects_out_of_range_confidence(schema, confidence) -> None:
    """Schema rejects out of range confidence."""
    payload = {
        "topics": [
            {
                "topic": "Update",
                "summary": "Summary text",
                "confidence": confidence,
                "public_safe": True,
            }
        ],
        "new_issues": [],
        "stats": {
            "notes_processed": 1,
            "topics_extracted": 1,
            "existing_matched": 0,
            "new_proposed": 0,
            "omitted": 0,
        },
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)
