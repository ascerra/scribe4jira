"""Unit tests for post-scribe gates and Jira writes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from conftest import apply_post_scribe_mode, write_agent_result

from scribe4jira.config import ScribeConfig
from scribe4jira.gates import ISSUE_BANNER, MAX_BODY_LEN, gate_reason
from scribe4jira.jira_client import IssueLookupError
from scribe4jira.jira_formatting import keys_to_linkify
from scribe4jira.post_scribe import (
    PostScribeSummary,
    _extract_notes_url,
    _load_known_issue_keys,
    _reject_content,
    run,
    validate_and_fix_parent,
)

pytestmark = pytest.mark.usefixtures("scribe_env")


@pytest.mark.parametrize(
    ("title", "body", "confidence", "threshold", "comment", "expected_error_snippet"),
    [
        ("Title", "Body", 0.4, 0.6, True, "confidence 0.4 below threshold 0.6"),
        ("Title", "Contact alice@example.com", 0.9, 0.6, True, "contains sensitive content"),
        ("Title", "Use ```python\nprint('x')\n```", 0.9, 0.6, True, "comment contains code block"),
        ("x" * 201, "Body", 0.9, 0.6, False, "summary length 201 exceeds max 200"),
        ("Title", "Clean summary", 0.9, 0.6, True, None),  # valid case - should return None
        ("Title", "hello\u200bworld", 0.9, 0.6, True, "contains suspicious Unicode"),
        ("Title", "x" * 2001, 0.9, 0.6, True, "summary length 2001 exceeds max 2000"),
        ("Title", "x" * 15001, 0.9, 0.6, False, "description exceeds 15000"),
    ],
)
def test_gate_reason_scenarios(
    title, body, confidence, threshold, comment, expected_error_snippet
) -> None:
    """Gate reason scenarios."""
    reason = gate_reason(title, body, confidence, threshold, comment=comment)

    if expected_error_snippet is None:
        assert reason is None
    else:
        assert reason is not None
        assert expected_error_snippet in reason


def test_extract_notes_url_parses_markdown_link() -> None:
    """Extract notes url parses markdown link."""
    body = "Update details.\n\n[Meeting notes](https://example.com/doc)"

    url = _extract_notes_url(body)

    assert url == "https://example.com/doc"


def test_extract_notes_url_returns_empty_when_missing() -> None:
    """Extract notes url returns empty when missing."""
    assert _extract_notes_url("No link here") == ""


def test_load_known_issue_keys_reads_backlog_and_closed_files(scribe_env) -> None:
    """Load known issue keys reads backlog and closed files."""
    scribe_env.mkdir(parents=True, exist_ok=True)
    backlog = scribe_env / "backlog.json"
    closed = scribe_env / "closed-issues.json"
    backlog.write_text('[{"key": "TEST-1"}]', encoding="utf-8")
    closed.write_text('[{"key": "TEST-9"}]', encoding="utf-8")
    cfg = ScribeConfig.from_env()

    keys = _load_known_issue_keys(cfg)

    assert keys == {"TEST-1", "TEST-9"}


def test_load_known_issue_keys_skips_malformed_json(scribe_env) -> None:
    """Load known issue keys skips malformed json files."""
    scribe_env.mkdir(parents=True, exist_ok=True)
    backlog = scribe_env / "backlog.json"
    closed = scribe_env / "closed-issues.json"
    backlog.write_text('[{"key": "TEST-1"}]', encoding="utf-8")
    closed.write_text("not valid json", encoding="utf-8")
    cfg = ScribeConfig.from_env()

    keys = _load_known_issue_keys(cfg)

    assert keys == {"TEST-1"}


def test_keys_to_linkify_intersects_text_with_known_keys() -> None:
    """Keys to linkify intersects text with known keys outside markdown links."""
    keys = keys_to_linkify("Builds on TEST-42 and TEST-99", {"TEST-42"})

    assert keys == {"TEST-42"}


def test_keys_to_linkify_ignores_keys_inside_link_labels() -> None:
    """Keys to linkify ignores keys inside existing markdown link labels."""
    keys = keys_to_linkify(
        "[See TEST-42 docs](https://example.com) and TEST-43",
        {"TEST-42", "TEST-43"},
    )

    assert keys == {"TEST-43"}


def test_run_live_linkifies_related_keys_in_new_issue_description(
    monkeypatch, agent_result_payload, scribe_env
) -> None:
    """Run live linkifies related keys in new issue description."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="new_issues_only")
    scribe_env.mkdir(parents=True, exist_ok=True)
    (scribe_env / "backlog.json").write_text('[{"key": "TEST-42"}]', encoding="utf-8")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["description"] = (
        "## Related\nBuilds on TEST-42 and TEST-99"
    )
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()
    mock_jira.create_issue.return_value = {"key": "TEST-100"}

    run(cfg, jira_client=mock_jira)

    fields = mock_jira.create_issue.call_args.args[0]
    assert fields["description"] == (
        "## Related\nBuilds on [TEST-42](https://jira.example.com/browse/TEST-42) and TEST-99"
    )


def test_reject_content_increments_summary_counters() -> None:
    """Reject content increments summary counters."""
    summary = PostScribeSummary()

    _reject_content(summary, 1, 3, "hr")

    assert summary.rejected == 1
    assert summary.content_gate_rejections == 1


def test_run_raises_when_agent_result_missing(monkeypatch) -> None:
    """Run raises when agent result missing."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="all")
    cfg = ScribeConfig.from_env()

    with pytest.raises(RuntimeError, match="Missing agent result"):
        run(cfg)


def test_run_raises_for_invalid_mode(monkeypatch) -> None:
    """Run raises for invalid mode."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="invalid")
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg)

    with pytest.raises(RuntimeError, match="SCRIBE_MODE must be all"):
        run(cfg)


def test_run_proceeds_when_dry_run_env_absent(workspace_config, monkeypatch) -> None:
    """SCRIBE_DRY_RUN no longer needs to exist in os.environ at runtime.

    In multi-tenant mode dry_run is set via team YAML (injected into env before
    ScribeConfig is built). The cfg object already carries the baked-in value.
    """
    monkeypatch.delenv("SCRIBE_DRY_RUN", raising=False)
    mock_jira = MagicMock()
    mock_jira.issue_exists.return_value = True
    mock_jira.comment_has_notes_url.return_value = False

    summary = run(workspace_config, jira_client=mock_jira)

    assert summary is not None


def test_run_dry_run_records_comments_without_jira_writes(
    monkeypatch, agent_result_payload, mock_post_jira
) -> None:
    """Run dry run records comments without jira writes."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="all")
    agent_result_payload["new_issues"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=mock_post_jira)

    assert summary.topics_processed == 1
    assert summary.comment_records == [("TEST-1", "Pipeline update")]
    assert summary.comments_posted == 0
    assert summary.issue_links[0].key == "TEST-1"
    mock_post_jira.add_comment.assert_not_called()


def test_run_live_posts_comment_when_issue_exists(
    monkeypatch, agent_result_payload, mock_post_jira
) -> None:
    """Run live posts comment when issue exists."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="comments_only")
    agent_result_payload["new_issues"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=mock_post_jira)

    assert summary.comments_posted == 1
    assert len(summary.issue_links) == 1
    assert summary.issue_links[0].action == "updated"
    assert summary.issue_links[0].url == "https://jira.example.com/browse/TEST-1"
    mock_post_jira.add_comment.assert_called_once_with(
        "TEST-1",
        agent_result_payload["topics"][0]["summary"],
        notes_url="https://example.com/notes",
    )


def test_run_skips_duplicate_comment(monkeypatch, agent_result_payload, mock_post_jira) -> None:
    """Run skips duplicate comment."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="comments_only")
    agent_result_payload["new_issues"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_post_jira.comment_has_notes_url.return_value = True

    summary = run(cfg, jira_client=mock_post_jira)

    assert summary.comments_posted == 0
    mock_post_jira.add_comment.assert_not_called()


def test_run_rejects_unknown_issue(monkeypatch, agent_result_payload, mock_post_jira) -> None:
    """Run rejects unknown issue."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="comments_only")
    agent_result_payload["new_issues"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_post_jira.issue_exists.return_value = False

    summary = run(cfg, jira_client=mock_post_jira)

    assert summary.rejected == 1
    assert summary.comments_posted == 0


def test_run_skips_comment_when_issue_lookup_fails(
    monkeypatch, agent_result_payload, mock_post_jira, capsys
) -> None:
    """Run skips comment when Jira lookup fails (non-404)."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="comments_only")
    agent_result_payload["new_issues"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_post_jira.issue_exists.side_effect = IssueLookupError(
        "Could not verify Jira issue TEST-1: transport error"
    )

    summary = run(cfg, jira_client=mock_post_jira)

    assert summary.rejected == 1
    assert summary.comments_posted == 0
    mock_post_jira.add_comment.assert_not_called()
    captured = capsys.readouterr()
    assert "could not verify Jira issue" in captured.out
    assert "unknown Jira issue" not in captured.out


def test_run_comments_only_skips_new_issues(monkeypatch, mock_post_jira) -> None:
    """Run comments only skips new issues."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="comments_only")
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg)

    summary = run(cfg, jira_client=mock_post_jira)

    assert summary.created_records == []
    mock_post_jira.create_issue.assert_not_called()


def test_run_new_issues_only_skips_comments(monkeypatch) -> None:
    """Run new issues only skips comments."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="new_issues_only")
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.topics_processed == 0
    assert summary.comment_records == []


def test_run_rejects_unsafe_topic(monkeypatch, agent_result_payload) -> None:
    """Run rejects unsafe topic."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="all")
    agent_result_payload["topics"][0]["public_safe"] = False
    agent_result_payload["topics"][0]["public_safe_category"] = "hr"
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.content_gate_rejections == 1
    assert summary.comment_records == []


def test_run_skips_topic_without_issue_key(monkeypatch, agent_result_payload) -> None:
    """Run skips topic without issue key."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="comments_only")
    agent_result_payload["topics"][0]["existing_issue_id"] = None
    agent_result_payload["new_issues"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.comment_records == []


def test_run_omits_topic_with_reason(monkeypatch, agent_result_payload) -> None:
    """Run omits topic with reason."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="comments_only")
    agent_result_payload["topics"][0]["omit_reason"] = "Low relevance"
    agent_result_payload["new_issues"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.comment_records == []


def test_run_rejects_low_confidence_comment(monkeypatch, agent_result_payload) -> None:
    """Run rejects low confidence comment."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="comments_only")
    agent_result_payload["topics"][0]["confidence"] = 0.1
    agent_result_payload["new_issues"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.rejected == 1


def test_run_rejects_unsafe_new_issue(monkeypatch, agent_result_payload) -> None:
    """Run rejects unsafe new issue."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["public_safe"] = False
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.content_gate_rejections == 1
    assert summary.created_records == []


def test_run_rejects_low_confidence_new_issue(monkeypatch, agent_result_payload) -> None:
    """Run rejects low confidence new issue."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["confidence"] = 0.1
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.rejected == 1


def test_run_rejects_oversized_new_issue_description(monkeypatch, agent_result_payload) -> None:
    """Run rejects oversized new issue description."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["description"] = "x" * (
        MAX_BODY_LEN - len(ISSUE_BANNER) + 1
    )
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.rejected == 1


def test_run_live_creates_jira_issue(monkeypatch, agent_result_payload) -> None:
    """Run live creates jira issue."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["story_points_suggestion"] = 5
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()
    mock_jira.create_issue.return_value = {"key": "TEST-99"}

    summary = run(cfg, jira_client=mock_jira)

    assert summary.issues_created == 1
    assert summary.issue_links[-1].key == "TEST-99"
    assert summary.issue_links[-1].action == "created"
    mock_jira.create_issue.assert_called_once()
    call_kwargs = mock_jira.create_issue.call_args.kwargs
    assert call_kwargs["story_points_suggestion"] == 5


def test_run_live_creates_issue_with_parent_issue_id(monkeypatch, agent_result_payload) -> None:
    """Run live creates issue with parent field when parent_issue_id is provided."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["parent_issue_id"] = "TEST-10"
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()
    mock_jira.issue_exists.return_value = True
    mock_jira.create_issue.return_value = {"key": "TEST-100"}

    summary = run(cfg, jira_client=mock_jira)

    assert summary.issues_created == 1
    fields = mock_jira.create_issue.call_args.args[0]
    assert fields["parent"] == {"key": "TEST-10"}
    mock_jira.issue_exists.assert_called_with("TEST-10")


def test_run_live_strips_nonexistent_parent_and_creates(monkeypatch, agent_result_payload) -> None:
    """Non-existent parent on a Task is stripped; issue is still created."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["parent_issue_id"] = "TEST-999"
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()
    mock_jira.issue_exists.return_value = False
    mock_jira.create_issue.return_value = {"key": "TEST-200"}

    summary = run(cfg, jira_client=mock_jira)

    assert summary.issues_created == 1
    fields = mock_jira.create_issue.call_args.args[0]
    assert "parent" not in fields


def test_run_dry_run_prints_parent_key(monkeypatch, agent_result_payload, capsys) -> None:
    """Dry run prints parent key info when parent_issue_id is set."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["parent_issue_id"] = "TEST-10"
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    run(cfg, jira_client=MagicMock())

    captured = capsys.readouterr()
    assert "(parent: TEST-10)" in captured.out


def test_run_dry_run_omits_parent_when_not_set(monkeypatch, agent_result_payload, capsys) -> None:
    """Dry run does not print parent info when parent_issue_id is absent."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="new_issues_only")
    agent_result_payload["topics"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    run(cfg, jira_client=MagicMock())

    captured = capsys.readouterr()
    assert "(parent:" not in captured.out


# --- Parent + issuetype validation / fixup ---


@pytest.mark.parametrize(
    ("issuetype", "parent_key", "exp_issuetype", "exp_parent", "exp_action"),
    [
        pytest.param(
            "Sub-task",
            None,
            "Task",
            None,
            "demoted to Task",
            id="subtask_no_parent_demotes_to_task",
        ),
        pytest.param(
            "Epic",
            "TEST-10",
            "Epic",
            None,
            "stripped parent",
            id="epic_with_parent_strips_parent",
        ),
        pytest.param("Task", "TEST-10", "Task", "TEST-10", None, id="task_with_parent_ok"),
        pytest.param("Task", None, "Task", None, None, id="task_without_parent_ok"),
        pytest.param("Story", "TEST-10", "Story", "TEST-10", None, id="story_with_parent_ok"),
        pytest.param(
            "Sub-task",
            "TEST-10",
            "Sub-task",
            "TEST-10",
            None,
            id="subtask_with_parent_ok",
        ),
        pytest.param("Epic", None, "Epic", None, None, id="epic_without_parent_ok"),
    ],
)
def test_validate_and_fix_parent(
    issuetype, parent_key, exp_issuetype, exp_parent, exp_action
) -> None:
    """Validate and fix parent repairs invalid issuetype+parent combinations."""
    result = validate_and_fix_parent(issuetype, parent_key)

    assert result.issuetype == exp_issuetype
    assert result.parent_key == exp_parent
    if exp_action is None:
        assert result.action is None
    else:
        assert result.action is not None
        assert exp_action in result.action


def test_run_demotes_subtask_without_parent_to_task(
    monkeypatch, agent_result_payload, capsys
) -> None:
    """Sub-task without parent_issue_id is demoted to Task and created."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["issuetype"] = "Sub-task"
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)

    summary = run(cfg, jira_client=MagicMock())

    assert summary.rejected == 0
    assert len(summary.created_records) == 1
    captured = capsys.readouterr()
    assert "demoted to Task" in captured.out
    assert "new Task" in captured.out


@pytest.mark.parametrize(
    "parent_issue_id",
    [None, "TEST-10"],
    ids=["without_parent", "with_parent"],
)
def test_run_rejects_epic_issuetype(
    monkeypatch, agent_result_payload, capsys, parent_issue_id
) -> None:
    """Epic-typed new issues are rejected before reaching Jira."""
    apply_post_scribe_mode(monkeypatch, dry_run=True, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["issuetype"] = "Epic"
    if parent_issue_id is not None:
        agent_result_payload["new_issues"][0]["parent_issue_id"] = parent_issue_id
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()

    summary = run(cfg, jira_client=mock_jira)

    assert summary.rejected == 1
    assert summary.created_records == []
    mock_jira.create_issue.assert_not_called()
    captured = capsys.readouterr()
    assert "Epic issuetype is not allowed" in captured.out


def test_run_strips_invalid_parent_on_task(monkeypatch, agent_result_payload, capsys) -> None:
    """Task with nonexistent parent has parent stripped and is created without it."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["parent_issue_id"] = "TEST-999"
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()
    mock_jira.issue_exists.return_value = False
    mock_jira.create_issue.return_value = {"key": "TEST-200"}

    summary = run(cfg, jira_client=mock_jira)

    assert summary.issues_created == 1
    fields = mock_jira.create_issue.call_args.args[0]
    assert "parent" not in fields
    captured = capsys.readouterr()
    assert "parent TEST-999 not found" in captured.out


def test_run_demotes_subtask_with_invalid_parent_to_task(
    monkeypatch, agent_result_payload, capsys
) -> None:
    """Sub-task with nonexistent parent is demoted to Task and created without parent."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["issuetype"] = "Sub-task"
    agent_result_payload["new_issues"][0]["parent_issue_id"] = "TEST-999"
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()
    mock_jira.issue_exists.return_value = False
    mock_jira.create_issue.return_value = {"key": "TEST-201"}

    summary = run(cfg, jira_client=mock_jira)

    assert summary.issues_created == 1
    fields = mock_jira.create_issue.call_args.args[0]
    assert fields["issuetype"] == {"name": "Task"}
    assert "parent" not in fields
    captured = capsys.readouterr()
    assert "demoting Sub-task to Task" in captured.out


def test_run_skips_new_issue_when_parent_lookup_fails(
    monkeypatch, agent_result_payload, capsys
) -> None:
    """Run skips new issue when parent existence check hits a non-404 Jira error."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="new_issues_only")
    agent_result_payload["topics"] = []
    agent_result_payload["new_issues"][0]["parent_issue_id"] = "TEST-10"
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()
    mock_jira.issue_exists.side_effect = IssueLookupError(
        "Could not verify Jira issue TEST-10: transport error"
    )

    summary = run(cfg, jira_client=mock_jira)

    assert summary.issues_created == 0
    assert summary.rejected == 1
    mock_jira.create_issue.assert_not_called()
    captured = capsys.readouterr()
    assert "could not verify parent TEST-10" in captured.out
    assert "not found" not in captured.out


def test_run_survives_jira_create_failure(monkeypatch, agent_result_payload, capsys) -> None:
    """Pipeline does not crash when Jira rejects a create — issue is skipped."""
    apply_post_scribe_mode(monkeypatch, dry_run=False, mode="new_issues_only")
    agent_result_payload["topics"] = []
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg, agent_result_payload)
    mock_jira = MagicMock()
    mock_jira.create_issue.side_effect = RuntimeError("Jira API POST /issue failed (400): bad")

    summary = run(cfg, jira_client=mock_jira)

    assert summary.issues_created == 0
    assert summary.rejected == 1
    assert summary.created_records == []
    captured = capsys.readouterr()
    assert "FAILED:" in captured.out
