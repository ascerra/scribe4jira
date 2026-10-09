"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scribe4jira.config import ScribeConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIBE_RESULT_SCHEMA_PATH = (
    REPO_ROOT / ".fullsend/scripts/scribe4jira/data/scribe-result.schema.json"
)

REQUIRED_ENV = {
    "JIRA_SERVER": "https://jira.example.com",
    "JIRA_EMAIL": "scribe@example.com",
    "JIRA_API_TOKEN": "test-token",
    "JIRA_PROJECT_KEY": "TEST",
}

CUTOFF_ISO = "2026-06-01T00:00:00Z"
DEFAULT_SEARCH_QUERY = "standup"
DEFAULT_NAME_FILTER = "team"

DEFAULT_DRIVE_FILE = {
    "id": "doc-1",
    "name": "Standup notes",
    "webViewLink": "https://example.com/doc-1",
}

SAMPLE_AGENT_RESULT = {
    "topics": [
        {
            "topic": "Pipeline update",
            "summary": "Team discussed CI wiring.\n\n[Meeting notes](https://example.com/notes)",
            "existing_issue_id": "TEST-1",
            "confidence": 0.9,
            "public_safe": True,
            "public_safe_category": None,
            "omit_reason": None,
        }
    ],
    "new_issues": [
        {
            "project_id": "TEST",
            "summary": "Add CI pipeline",
            "description": "Wire pytest into the pipeline.",
            "issuetype": "Task",
            "priority": "Undefined",
            "labels": ["meeting-notes"],
            "components": [],
            "assignee": None,
            "confidence": 0.85,
            "public_safe": True,
            "public_safe_category": None,
        }
    ],
    "stats": {
        "notes_processed": 1,
        "topics_extracted": 1,
        "existing_matched": 1,
        "new_proposed": 1,
        "omitted": 0,
    },
}


def apply_required_env(monkeypatch: pytest.MonkeyPatch, exclude: str | None = None) -> None:
    """Set required Scribe env vars, optionally omitting one key."""
    for name, value in REQUIRED_ENV.items():
        if name == exclude:
            monkeypatch.delenv(name, raising=False)
            continue
        monkeypatch.setenv(name, value)


def apply_drive_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    search_query: str = DEFAULT_SEARCH_QUERY,
    name_filter: str | None = None,
) -> None:
    """Configure env for Google Drive notes ingestion."""
    monkeypatch.setenv("SCRIBE_NOTES_SOURCE", "drive")
    monkeypatch.setenv("SCRIBE_SEARCH_QUERY", search_query)
    if name_filter is not None:
        monkeypatch.setenv("SCRIBE_NAME_FILTER", name_filter)


def apply_file_notes_env(monkeypatch: pytest.MonkeyPatch, notes_file: str | Path) -> None:
    """Configure env for local file notes ingestion."""
    monkeypatch.setenv("SCRIBE_NOTES_SOURCE", "file")
    monkeypatch.setenv("SCRIBE_NOTES_FILE", str(notes_file))


def apply_post_scribe_mode(monkeypatch: pytest.MonkeyPatch, *, dry_run: bool, mode: str) -> None:
    """Configure SCRIBE_DRY_RUN and SCRIBE_MODE for post-scribe tests."""
    monkeypatch.setenv("SCRIBE_DRY_RUN", "true" if dry_run else "false")
    monkeypatch.setenv("SCRIBE_MODE", mode)


def make_drive_list_response(files: list[dict]) -> MagicMock:
    """Build a mocked Drive API list-files response."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"files": files}
    return response


def write_agent_result(cfg: ScribeConfig, payload: dict | None = None) -> None:
    """Write SAMPLE_AGENT_RESULT (or payload) to the workspace result file."""
    cfg.ensure_workspace()
    cfg.result_file.write_text(
        json.dumps(payload or SAMPLE_AGENT_RESULT, indent=2),
        encoding="utf-8",
    )


@pytest.fixture
def scribe_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Minimal isolated environment for ScribeConfig.from_env()."""
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("scribe4jira.config._load_dotenv_if_present", lambda: None)
    monkeypatch.setenv("SCRIBE_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("SCRIBE_DRY_RUN", "true")
    apply_required_env(monkeypatch)
    monkeypatch.delenv("SCRIBE_DRIVE_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("SCRIBE_SEARCH_QUERY", raising=False)
    monkeypatch.delenv("SCRIBE_NAME_FILTER", raising=False)
    return workspace


@pytest.fixture
def scribe_config(scribe_env: Path) -> ScribeConfig:
    """ScribeConfig loaded from the isolated scribe_env fixture."""
    return ScribeConfig.from_env()


@pytest.fixture
def drive_env(scribe_env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scribe env configured for Google Drive notes (default search query)."""
    apply_drive_env(monkeypatch)
    return scribe_env


@pytest.fixture
def drive_config(drive_env: Path) -> ScribeConfig:
    """ScribeConfig with Drive notes source enabled."""
    return ScribeConfig.from_env()


@pytest.fixture
def drive_workspace_config(drive_env: Path) -> ScribeConfig:
    """ScribeConfig with workspace initialized for Drive tests."""
    cfg = ScribeConfig.from_env()
    cfg.ensure_workspace()
    return cfg


@pytest.fixture
def drive_env_with_name_filter(drive_env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Drive env with SCRIBE_NAME_FILTER set."""
    monkeypatch.setenv("SCRIBE_NAME_FILTER", DEFAULT_NAME_FILTER)
    return drive_env


@pytest.fixture
def notes_file(tmp_path: Path) -> Path:
    """Temporary local notes file with sample content."""
    path = tmp_path / "notes.txt"
    path.write_text("Notes", encoding="utf-8")
    return path


@pytest.fixture
def file_env(scribe_env: Path, monkeypatch: pytest.MonkeyPatch, notes_file: Path) -> Path:
    """Scribe env configured for local file notes ingestion."""
    """Scribe env configured for local file notes."""
    apply_file_notes_env(monkeypatch, notes_file)
    return scribe_env


@pytest.fixture
def mock_jira_client() -> MagicMock:
    """Jira client mock with empty backlog and closed-issue responses."""
    jira = MagicMock()
    jira.fetch_backlog.return_value = []
    jira.fetch_recently_closed.return_value = []
    return jira


@pytest.fixture
def agent_result_payload() -> dict:
    """Deep copy of SAMPLE_AGENT_RESULT for per-test mutation."""
    return json.loads(json.dumps(SAMPLE_AGENT_RESULT))


@pytest.fixture
def mock_post_jira():
    """Jira client mock with common post-scribe defaults."""
    jira = MagicMock()
    jira.issue_exists.return_value = True
    jira.comment_has_notes_url.return_value = False
    return jira


@pytest.fixture
def workspace_config(scribe_env: Path, monkeypatch: pytest.MonkeyPatch) -> ScribeConfig:
    """ScribeConfig with agent-result.json written for post-scribe tests."""
    monkeypatch.setenv("SCRIBE_DRY_RUN", "true")
    monkeypatch.setenv("SCRIBE_MODE", "all")
    cfg = ScribeConfig.from_env()
    write_agent_result(cfg)
    return cfg
