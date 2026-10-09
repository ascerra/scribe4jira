#!/usr/bin/env python3
"""Fetch meeting notes and Jira backlog context for the Scribe agent."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scribe4jira.config import ScribeConfig
from scribe4jira.jira_client import JiraClient
from scribe4jira.logging_config import get_logger
from scribe4jira.security import strip_suspicious_unicode, structural_scrub

logger = get_logger(__name__)

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account
except ImportError:  # pragma: no cover - validated at runtime
    service_account = None  # type: ignore[assignment]
    GoogleAuthRequest = None  # type: ignore[assignment,misc]

try:
    import requests
except ImportError:  # pragma: no cover - validated at runtime
    requests = None  # type: ignore[assignment]

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
MAX_DOC_BYTES = 2 * 1024 * 1024


def run(config: ScribeConfig | None = None, jira_client: JiraClient | None = None) -> Path:
    """Fetch Jira backlog and meeting notes, write to workspace.

        config: Optional ScribeConfig instance (defaults to from_env())
        jira_client: Optional JiraClient instance (defaults to new instance)

    returns: Path to workspace directory
    """
    logger.debug("starting_pre_scribe", extra={"stage": "pre-scribe"})
    cfg = config or ScribeConfig.from_env()
    logger.debug(
        "config_loaded", extra={"project": cfg.jira_project_key, "notes_source": cfg.notes_source}
    )
    cfg.ensure_workspace()
    logger.debug("workspace_ready", extra={"path": str(cfg.workspace_dir)})

    logger.debug("initializing_jira_client")
    jira = jira_client or JiraClient(cfg)
    backlog = jira.fetch_backlog(cfg.jira_project_key)
    closed = jira.fetch_recently_closed(cfg.jira_project_key)

    logger.debug("writing_backlog", extra={"path": str(cfg.backlog_file), "count": len(backlog)})
    cfg.backlog_file.write_text(json.dumps(backlog, indent=2), encoding="utf-8")
    closed_file = cfg.workspace_dir / "closed-issues.json"
    logger.debug("writing_closed_issues", extra={"path": str(closed_file), "count": len(closed)})
    closed_file.write_text(json.dumps(closed, indent=2), encoding="utf-8")

    cutoff = datetime.now(UTC) - timedelta(hours=cfg.lookback_hours)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    notes_url = ""
    docs_downloaded = 0

    if cfg.notes_source == "file":
        logger.debug("ingesting_local_notes", extra={"path": cfg.notes_file})
        docs_downloaded = _ingest_local_notes(cfg, cutoff_iso)
        url_file = cfg.notes_dir / "doc-0.url"
        if url_file.exists():
            notes_url = url_file.read_text(encoding="utf-8").strip()
        logger.debug("local_notes_ingested", extra={"count": docs_downloaded})
    else:
        logger.debug("ingesting_drive_notes")
        docs_downloaded, notes_url = _ingest_drive_notes(cfg, cutoff_iso)
        logger.debug("drive_notes_ingested", extra={"count": docs_downloaded})

    logger.debug("writing_metadata", extra={"path": str(cfg.meta_file)})
    meta = {
        "cutoff_date": cutoff_iso,
        "notes_url": notes_url,
        "jira_project_key": cfg.jira_project_key,
        "jira_server": cfg.jira_server,
        "docs_downloaded": docs_downloaded,
        "backlog_issues": len(backlog),
        "closed_issues": len(closed),
    }
    cfg.meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.debug("metadata_written")
    print(
        f"Pre-scribe complete: {docs_downloaded} note(s), "
        f"{len(backlog)} open + {len(closed)} closed Jira issues."
    )
    print(f"Workspace: {cfg.workspace_dir}")
    return cfg.workspace_dir


def _ingest_local_notes(cfg: ScribeConfig, cutoff_iso: str) -> int:
    """Read meeting notes from local file and write to workspace.

        cfg: ScribeConfig instance
        cutoff_iso: ISO 8601 cutoff date (not used for local files)

    returns: Number of documents ingested (always 1 for local file source)

    raises: RuntimeError: If notes file not configured or not found
    """
    if not cfg.notes_file:
        raise RuntimeError("SCRIBE_NOTES_FILE is required when SCRIBE_NOTES_SOURCE=file")
    source = Path(cfg.notes_file)
    if not source.exists():
        raise RuntimeError(f"Notes file not found: {source}")

    logger.debug("reading_local_notes", extra={"path": str(source)})
    raw = source.read_text(encoding="utf-8")
    logger.debug("scrubbing_notes_content")
    cleaned = structural_scrub(strip_suspicious_unicode(raw))
    target = cfg.notes_dir / "doc-0.txt"
    logger.debug("writing_cleaned_notes", extra={"path": str(target)})
    target.write_text(cleaned, encoding="utf-8")
    (cfg.notes_dir / "doc-0.url").write_text(source.as_uri(), encoding="utf-8")
    _ = cutoff_iso
    return 1


def _ingest_drive_notes(cfg: ScribeConfig, cutoff_iso: str) -> tuple[int, str]:
    """Search Google Drive for meeting notes and download to workspace.

        cfg: ScribeConfig instance
        cutoff_iso: ISO 8601 cutoff date for Drive query

    returns: Tuple of (number of documents downloaded, URL of first document)

    raises: RuntimeError: If Drive configuration missing or requests unavailable
    """
    if not cfg.search_query:
        raise RuntimeError("SCRIBE_SEARCH_QUERY is required when SCRIBE_NOTES_SOURCE=drive")
    if requests is None:
        raise RuntimeError("requests is required for Google Drive ingestion")
    if service_account is None:
        raise RuntimeError("google-auth is required for Google Drive ingestion")

    logger.debug("obtaining_drive_token")
    token = _drive_access_token(cfg.drive_credentials)
    logger.debug("drive_token_obtained")

    query = _build_drive_query(cfg, cutoff_iso)
    files = _fetch_drive_files(token, query)
    if not files and cfg.name_filter:
        print(
            "  Hint: SCRIBE_SEARCH_QUERY and SCRIBE_NAME_FILTER are combined with AND. "
            "Both substrings must appear in the document title."
        )

    notes_url = ""
    downloaded = 0
    for index, doc in enumerate(files):
        doc_id = doc["id"]
        doc_name = doc.get("name", doc_id)
        doc_url = doc.get("webViewLink", "")
        logger.debug("downloading_drive_doc", extra={"name": doc_name, "doc_id": doc_id})

        content = _export_doc_with_retry(token, doc_id)
        if not content:
            continue
        if len(content.encode("utf-8")) > MAX_DOC_BYTES:
            print(f"  WARNING: skipping {doc_name}; exceeds {MAX_DOC_BYTES} bytes")
            continue

        cleaned = structural_scrub(strip_suspicious_unicode(content))
        note_path = cfg.notes_dir / f"doc-{index}.txt"
        logger.debug("writing_drive_doc", extra={"path": str(note_path), "name": doc_name})
        note_path.write_text(cleaned, encoding="utf-8")
        (cfg.notes_dir / f"doc-{index}.url").write_text(doc_url, encoding="utf-8")
        logger.debug("drive_doc_saved", extra={"name": doc_name})
        if index == 0:
            notes_url = doc_url
        downloaded += 1

    return downloaded, notes_url


def _build_drive_query(cfg: ScribeConfig, cutoff_iso: str) -> str:
    """Build a Google Drive files.list query for meeting-note documents."""
    query = (
        f"name contains '{_escape_query(cfg.search_query)}' "
        "and mimeType = 'application/vnd.google-apps.document' "
        "and trashed = false "
        f"and createdTime > '{cutoff_iso}'"
    )
    if cfg.name_filter:
        query += f" and name contains '{_escape_query(cfg.name_filter)}'"
    return query


def _fetch_drive_files(token: str, query: str) -> list[dict[str, Any]]:
    """List Google Drive documents matching the query via Drive API v3."""
    if requests is None:
        raise RuntimeError("requests is required for Google Drive ingestion")
    print(f"[DEBUG] Drive query: {query}")
    params = {
        "q": query,
        "fields": "files(id,name,createdTime,modifiedTime,webViewLink)",
        "orderBy": "createdTime desc",
        "pageSize": 20,
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    print("[DEBUG] Google Drive API: listing files")
    response = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {token}"},
        params=params,  # type: ignore[arg-type]
        timeout=60,
    )
    response.raise_for_status()
    files = response.json().get("files", [])
    print(f"[DEBUG] Google Drive API success: found {len(files)} document(s)")
    return files


def _export_doc_with_retry(token: str, doc_id: str) -> str | None:
    """Export a Google Doc as plain text, retrying on transient server errors."""
    if requests is None:
        raise RuntimeError("requests is required for Google Drive ingestion")
    for attempt in range(1, 4):
        logger.debug("exporting_drive_doc", extra={"doc_id": doc_id, "attempt": attempt})
        response = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{doc_id}/export",
            headers={"Authorization": f"Bearer {token}"},
            params={"mimeType": "text/plain"},
            timeout=60,
        )
        if response.status_code == 200:
            logger.debug("drive_doc_exported", extra={"doc_id": doc_id})
            return response.text
        if response.status_code >= 500 and attempt < 3:
            wait = 2 ** (attempt - 1)
            logger.warning(
                "drive_export_retry",
                extra={"doc_id": doc_id, "status_code": response.status_code, "wait_seconds": wait},
            )
            print(f"  WARNING: export HTTP {response.status_code}, retrying in {wait}s")
            time.sleep(wait)
            continue
        logger.error(
            "drive_export_failed", extra={"doc_id": doc_id, "status_code": response.status_code}
        )
        print(f"  WARNING: export failed with HTTP {response.status_code}")
        return None
    return None


def _drive_access_token(credentials_path: str) -> str:
    """Get Google Drive API access token from service account credentials.

        credentials_path: Path to service account JSON key file

    returns: OAuth 2.0 access token with drive.readonly scope

    raises: RuntimeError: If credentials missing, not found, or token refresh fails
    """
    logger.debug("loading_drive_credentials", extra={"path": credentials_path})
    if not credentials_path:
        raise RuntimeError(
            "SCRIBE_DRIVE_CREDENTIALS or GOOGLE_APPLICATION_CREDENTIALS must be set "
            "(path to a service account JSON key with drive.readonly access)"
        )
    if not Path(credentials_path).is_file():
        raise RuntimeError(
            f"Drive credentials file not found: {credentials_path}. "
            "Use an absolute path or a path relative to the scribe/ directory."
        )
    creds = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=[DRIVE_SCOPE],
    )
    creds.refresh(GoogleAuthRequest())
    if not creds.token:
        raise RuntimeError("Could not obtain Drive-scoped access token")
    return creds.token


def _escape_query(value: str) -> str:
    """Escape single quotes in Google Drive API query strings.

        value: Query string value to escape

    returns: Escaped query string
    """
    return value.replace("'", "\\'")


if __name__ == "__main__":
    run()
