"""Check host write boundaries and credential handling."""

import pytest

from scribe4jira.config import ScribeConfig
from scribe4jira.fullsend import check_issue_limit, check_project_scope


@pytest.mark.parametrize("value", ["ture", "", "maybe"])
def test_invalid_dry_run_fails_closed(scribe_env, monkeypatch, value):
    monkeypatch.setenv("SCRIBE_DRY_RUN", value)
    with pytest.raises(ValueError, match="boolean"):
        ScribeConfig.from_env(load_dotenv=False)


def test_token_file_used_without_environment_copy(scribe_env, tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("synthetic-file-credential\n")
    monkeypatch.setenv("JIRA_API_TOKEN_FILE", str(token))
    monkeypatch.delenv("JIRA_API_TOKEN")
    cfg = ScribeConfig.from_env(load_dotenv=False)
    assert cfg.jira_token == "synthetic-file-credential"


@pytest.mark.parametrize(
    "result",
    [
        {"new_issues": [{"project_id": "OTHER"}]},
        {"new_issues": [{"project_id": "TEST", "parent_issue_id": "OTHER-1"}]},
        {"topics": [{"existing_issue_id": "OTHER-1"}]},
    ],
)
def test_cross_project_output_is_rejected(result):
    with pytest.raises(ValueError, match="different Jira project"):
        check_project_scope(result, "TEST")


def test_configured_project_is_accepted():
    check_project_scope(
        {"new_issues": [{"project_id": "TEST"}], "topics": [{"existing_issue_id": "TEST-1"}]},
        "TEST",
    )


def test_issue_limit_blocks_multiple_writes(monkeypatch):
    monkeypatch.setenv("SCRIBE_MAX_NEW_ISSUES", "1")
    check_issue_limit({"new_issues": [{}]})
    with pytest.raises(ValueError, match="exceed"):
        check_issue_limit({"new_issues": [{}, {}]})


@pytest.mark.parametrize("value", ["", "-1", "one"])
def test_invalid_issue_limit_fails_closed(monkeypatch, value):
    monkeypatch.setenv("SCRIBE_MAX_NEW_ISSUES", value)
    with pytest.raises(ValueError, match="nonnegative integer"):
        check_issue_limit({"new_issues": []})
