"""Unit tests for pre-scribe ingestion and Jira context fetch."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from conftest import (
    CUTOFF_ISO,
    DEFAULT_DRIVE_FILE,
    DEFAULT_NAME_FILTER,
    apply_drive_env,
    apply_file_notes_env,
    make_drive_list_response,
)

from scribe4jira.config import ScribeConfig
from scribe4jira.pre_scribe import (
    _build_drive_query,
    _escape_query,
    _export_doc_with_retry,
    _fetch_drive_files,
    _ingest_drive_notes,
    _ingest_local_notes,
    run,
)


def test_escape_query_escapes_single_quotes() -> None:
    """Escape query escapes single quotes."""
    assert _escape_query("team's notes") == "team\\'s notes"


@pytest.mark.parametrize(
    ("raw_content", "should_exist", "should_not_exist"),
    [
        ("Invited alice@example.com\nSprint planning", "Sprint planning", "alice@example.com"),
        ("My phone is 050-1234567\nRetro meeting", "Retro meeting", "050-1234567"),
        ("Password: " + "x" * 20 + "\nReview notes", "Review notes", "x" * 20),
    ],
)
def test_ingest_local_notes_scrubs_various_pii(
    scribe_env, tmp_path, monkeypatch, raw_content, should_exist, should_not_exist
) -> None:
    """Ingest local notes scrubs various pii."""
    source = tmp_path / "notes.txt"
    source.write_text(raw_content, encoding="utf-8")
    apply_file_notes_env(monkeypatch, source)
    cfg = ScribeConfig.from_env()
    cfg.ensure_workspace()

    _ingest_local_notes(cfg, CUTOFF_ISO)

    cleaned = (cfg.notes_dir / "doc-0.txt").read_text(encoding="utf-8")
    assert should_exist in cleaned
    assert should_not_exist not in cleaned


def test_ingest_local_notes_raises_when_file_missing(scribe_env, monkeypatch) -> None:
    """Ingest local notes raises when file missing."""
    apply_file_notes_env(monkeypatch, "/missing/notes.txt")
    cfg = ScribeConfig.from_env()

    with pytest.raises(RuntimeError, match="Notes file not found"):
        _ingest_local_notes(cfg, CUTOFF_ISO)


def test_build_drive_query_includes_search_and_cutoff(drive_config) -> None:
    """Build drive query includes search and cutoff."""
    query = _build_drive_query(drive_config, CUTOFF_ISO)

    assert "name contains 'standup'" in query
    assert "mimeType = 'application/vnd.google-apps.document'" in query
    assert "trashed = false" in query
    assert f"createdTime > '{CUTOFF_ISO}'" in query


def test_build_drive_query_includes_name_filter(drive_env_with_name_filter) -> None:
    """Build drive query includes name filter."""
    cfg = ScribeConfig.from_env()

    query = _build_drive_query(cfg, CUTOFF_ISO)

    assert f"name contains '{DEFAULT_NAME_FILTER}'" in query


def test_build_drive_query_escapes_single_quotes_in_filters(scribe_env, monkeypatch) -> None:
    """Build drive query escapes single quotes in filters."""
    apply_drive_env(monkeypatch, search_query="team's standup", name_filter="Q2'26")
    cfg = ScribeConfig.from_env()

    query = _build_drive_query(cfg, CUTOFF_ISO)

    assert "name contains 'team\\'s standup'" in query
    assert "name contains 'Q2\\'26'" in query


@patch("scribe4jira.pre_scribe.requests.get")
def test_fetch_drive_files_returns_file_list(mock_get) -> None:
    """Fetch drive files returns file list."""
    mock_get.return_value = make_drive_list_response([DEFAULT_DRIVE_FILE])

    files = _fetch_drive_files("drive-token", "name contains 'standup'")

    assert len(files) == 1
    assert files[0]["id"] == "doc-1"
    mock_get.assert_called_once_with(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": "Bearer drive-token"},
        params={
            "q": "name contains 'standup'",
            "fields": "files(id,name,createdTime,modifiedTime,webViewLink)",
            "orderBy": "createdTime desc",
            "pageSize": 20,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
        timeout=60,
    )


@patch("scribe4jira.pre_scribe.requests.get")
def test_fetch_drive_files_raises_on_http_error(mock_get) -> None:
    """Fetch drive files raises on http error."""
    list_response = MagicMock()
    list_response.raise_for_status.side_effect = RuntimeError("403 forbidden")
    mock_get.return_value = list_response

    with pytest.raises(RuntimeError, match="403 forbidden"):
        _fetch_drive_files("drive-token", "name contains 'standup'")


@patch("scribe4jira.pre_scribe.requests.get")
def test_export_doc_with_retry_returns_text_on_success(mock_get) -> None:
    """Export doc with retry returns text on success."""
    response = MagicMock()
    response.status_code = 200
    response.text = "Meeting notes body"
    mock_get.return_value = response

    content = _export_doc_with_retry("token", "doc-123")

    assert content == "Meeting notes body"


@patch("scribe4jira.pre_scribe.time.sleep")
@patch("scribe4jira.pre_scribe.requests.get")
def test_export_doc_with_retry_retries_server_errors(mock_get, _mock_sleep) -> None:
    """Export doc with retry retries server errors."""
    failing = MagicMock(status_code=503, text="")
    success = MagicMock(status_code=200, text="Recovered notes")
    mock_get.side_effect = [failing, success]

    content = _export_doc_with_retry("token", "doc-123")

    assert content == "Recovered notes"
    assert mock_get.call_count == 2


@patch("scribe4jira.pre_scribe._ingest_local_notes", return_value=1)
def test_run_uses_local_notes_source(mock_ingest, file_env, notes_file) -> None:
    """Run uses local notes source."""
    mock_jira = MagicMock()
    mock_jira.fetch_backlog.return_value = [{"key": "TEST-1"}]
    mock_jira.fetch_recently_closed.return_value = []

    workspace = run(jira_client=mock_jira)

    mock_ingest.assert_called_once()
    assert json.loads((workspace / "backlog.json").read_text(encoding="utf-8")) == [
        {"key": "TEST-1"}
    ]
    assert (
        json.loads((workspace / "scribe-meta.json").read_text(encoding="utf-8"))["docs_downloaded"]
        == 1
    )


@patch("scribe4jira.pre_scribe._ingest_drive_notes", return_value=(2, "https://example.com/doc"))
def test_run_uses_drive_notes_source(mock_ingest, drive_env) -> None:
    """Run uses drive notes source."""
    mock_jira = MagicMock()
    mock_jira.fetch_backlog.return_value = []
    mock_jira.fetch_recently_closed.return_value = [{"key": "TEST-9"}]

    workspace = run(jira_client=mock_jira)

    mock_ingest.assert_called_once()
    meta = json.loads((workspace / "scribe-meta.json").read_text(encoding="utf-8"))
    assert meta["notes_url"] == "https://example.com/doc"
    assert meta["docs_downloaded"] == 2


@patch("scribe4jira.pre_scribe.requests.get")
@patch("scribe4jira.pre_scribe._drive_access_token", return_value="drive-token")
def test_ingest_drive_notes_downloads_and_scrubs_documents(
    _mock_token,
    mock_get,
    drive_workspace_config,
) -> None:
    """Ingest drive notes downloads and scrubs documents."""
    export_response = MagicMock(status_code=200, text="Invited bob@example.com\nDecisions")
    mock_get.side_effect = [make_drive_list_response([DEFAULT_DRIVE_FILE]), export_response]

    downloaded, notes_url = _ingest_drive_notes(drive_workspace_config, CUTOFF_ISO)

    assert downloaded == 1
    assert notes_url == "https://example.com/doc-1"
    cleaned = (drive_workspace_config.notes_dir / "doc-0.txt").read_text(encoding="utf-8")
    assert "Invited" not in cleaned
    assert "Decisions" in cleaned


def test_ingest_local_notes_raises_when_notes_file_unset(scribe_config) -> None:
    """Ingest local notes raises when notes file unset."""
    with pytest.raises(RuntimeError, match="SCRIBE_NOTES_FILE is required"):
        _ingest_local_notes(scribe_config, CUTOFF_ISO)


def test_ingest_drive_notes_raises_without_search_query(scribe_env, monkeypatch) -> None:
    """Ingest drive notes raises without search query."""
    monkeypatch.delenv("SCRIBE_SEARCH_QUERY", raising=False)
    cfg = ScribeConfig.from_env()

    with pytest.raises(RuntimeError, match="SCRIBE_SEARCH_QUERY is required"):
        _ingest_drive_notes(cfg, CUTOFF_ISO)


def test_ingest_drive_notes_raises_when_requests_missing(drive_env, monkeypatch) -> None:
    """Ingest drive notes raises when requests missing."""
    monkeypatch.setattr("scribe4jira.pre_scribe.requests", None)
    cfg = ScribeConfig.from_env()

    with pytest.raises(RuntimeError, match="requests is required"):
        _ingest_drive_notes(cfg, CUTOFF_ISO)


def test_ingest_drive_notes_raises_when_google_auth_missing(drive_env, monkeypatch) -> None:
    """Ingest drive notes raises when google auth missing."""
    monkeypatch.setattr("scribe4jira.pre_scribe.service_account", None)
    cfg = ScribeConfig.from_env()

    with pytest.raises(RuntimeError, match="google-auth is required"):
        _ingest_drive_notes(cfg, CUTOFF_ISO)


@patch("scribe4jira.pre_scribe._fetch_drive_files", return_value=[])
@patch("scribe4jira.pre_scribe._drive_access_token", return_value="drive-token")
def test_ingest_drive_notes_applies_name_filter(
    _mock_token,
    mock_fetch,
    drive_env_with_name_filter,
) -> None:
    """Ingest drive notes applies name filter."""
    cfg = ScribeConfig.from_env()
    cfg.ensure_workspace()

    downloaded, notes_url = _ingest_drive_notes(cfg, CUTOFF_ISO)

    assert downloaded == 0
    assert notes_url == ""
    assert f"name contains '{DEFAULT_NAME_FILTER}'" in mock_fetch.call_args.args[1]


@patch("scribe4jira.pre_scribe._export_doc_with_retry", return_value=None)
@patch("scribe4jira.pre_scribe.requests.get")
@patch("scribe4jira.pre_scribe._drive_access_token", return_value="drive-token")
def test_ingest_drive_notes_skips_failed_export(
    _mock_token,
    mock_get,
    _mock_export,
    drive_workspace_config,
) -> None:
    """Ingest drive notes skips failed export."""
    mock_get.return_value = make_drive_list_response([DEFAULT_DRIVE_FILE])

    downloaded, notes_url = _ingest_drive_notes(drive_workspace_config, CUTOFF_ISO)

    assert downloaded == 0
    assert notes_url == ""


@patch("scribe4jira.pre_scribe._export_doc_with_retry")
@patch("scribe4jira.pre_scribe.requests.get")
@patch("scribe4jira.pre_scribe._drive_access_token", return_value="drive-token")
def test_ingest_drive_notes_skips_oversized_document(
    _mock_token,
    mock_get,
    mock_export,
    drive_workspace_config,
) -> None:
    """Ingest drive notes skips oversized document."""
    mock_get.return_value = make_drive_list_response([DEFAULT_DRIVE_FILE])
    mock_export.return_value = "x" * ((2 * 1024 * 1024) + 1)

    downloaded, notes_url = _ingest_drive_notes(drive_workspace_config, CUTOFF_ISO)

    assert downloaded == 0
    assert notes_url == ""


@patch("scribe4jira.pre_scribe.time.sleep")
@patch("scribe4jira.pre_scribe.requests.get")
def test_export_doc_with_retry_exhausts_retries(mock_get, _mock_sleep) -> None:
    """Export doc with retry exhausts retries."""
    mock_get.return_value = MagicMock(status_code=503, text="server error")

    content = _export_doc_with_retry("token", "doc-503")

    assert content is None
    assert mock_get.call_count == 3


@patch("scribe4jira.pre_scribe.requests.get")
def test_export_doc_with_retry_returns_none_on_client_error(mock_get) -> None:
    """Export doc with retry returns none on client error."""
    mock_get.return_value = MagicMock(status_code=404, text="missing")

    content = _export_doc_with_retry("token", "doc-404")

    assert content is None


def test_drive_access_token_raises_when_path_missing(scribe_config) -> None:
    """Drive access token raises when path missing."""
    from scribe4jira.pre_scribe import _drive_access_token

    with pytest.raises(RuntimeError, match="Drive credentials file not found"):
        _drive_access_token("/missing/creds.json")


def test_drive_access_token_raises_when_path_empty(scribe_config) -> None:
    """Drive access token raises when path empty."""
    from scribe4jira.pre_scribe import _drive_access_token

    with pytest.raises(
        RuntimeError, match="SCRIBE_DRIVE_CREDENTIALS or GOOGLE_APPLICATION_CREDENTIALS"
    ):
        _drive_access_token("")


@patch("scribe4jira.pre_scribe.GoogleAuthRequest")
@patch("scribe4jira.pre_scribe.service_account.Credentials.from_service_account_file")
def test_drive_access_token_raises_when_refresh_returns_no_token(
    mock_from_file,
    _mock_auth_request,
    tmp_path,
) -> None:
    """Drive access token raises when refresh returns no token."""
    from scribe4jira.pre_scribe import _drive_access_token

    creds_path = tmp_path / "creds.json"
    creds_path.write_text("{}", encoding="utf-8")
    creds = MagicMock()
    creds.token = ""
    mock_from_file.return_value = creds

    with pytest.raises(RuntimeError, match="Could not obtain Drive-scoped access token"):
        _drive_access_token(str(creds_path))


@patch("scribe4jira.pre_scribe.GoogleAuthRequest")
@patch("scribe4jira.pre_scribe.service_account.Credentials.from_service_account_file")
def test_drive_access_token_returns_refreshed_token(
    mock_from_file,
    _mock_auth_request,
    tmp_path,
) -> None:
    """Drive access token returns refreshed token."""
    from scribe4jira.pre_scribe import _drive_access_token

    creds_path = tmp_path / "creds.json"
    creds_path.write_text("{}", encoding="utf-8")
    creds = MagicMock()
    creds.token = "fresh-token"
    mock_from_file.return_value = creds

    token = _drive_access_token(str(creds_path))

    assert token == "fresh-token"


def test_run_reads_notes_url_from_local_file(file_env, mock_jira_client) -> None:
    """Run reads notes url from local file."""
    workspace = run(jira_client=mock_jira_client)
    meta = json.loads((workspace / "scribe-meta.json").read_text(encoding="utf-8"))

    assert meta["notes_url"].startswith("file:")
    assert meta["docs_downloaded"] == 1
