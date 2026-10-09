"""Minimal Jira REST client for Jira Cloud (REST API v3)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

import requests

from scribe4jira.config import ScribeConfig
from scribe4jira.jira_formatting import (
    JiraTheme,
    agent_banner_text,
    # comment_footer_text,
    story_point_suggestion_text,
)
from scribe4jira.logging_config import get_logger

logger = get_logger(__name__)


class IssueLookupError(RuntimeError):
    """Jira API or transport failure while checking issue existence (non-404)."""


def _runtime_error_status(exc: RuntimeError) -> int | None:
    """Extract HTTP status code from a JiraClient._request RuntimeError message."""
    match = re.search(r"failed \((\d+)\):", str(exc))
    return int(match.group(1)) if match else None


class JiraClient:
    """Thin wrapper around Jira REST API v3."""

    def __init__(self, config: ScribeConfig) -> None:
        """Initialize Jira client with configuration.

        config: ScribeConfig instance with Jira credentials and server URL
        """
        self._config = config
        self._server = config.jira_server.rstrip("/")
        self._base = resolve_jira_api_base(
            self._server,
            cloud_id=config.jira_cloud_id,
            use_service_account=config.jira_use_service_account,
        )
        self._auth_header = _basic_auth_header(config.jira_email, config.jira_token)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Make authenticated HTTP request to Jira REST API.

        method: HTTP method (GET, POST, PUT, DELETE)
        path: API path relative to /rest/api/3
        payload: Optional JSON payload for POST/PUT requests

        returns: Parsed JSON response or None for empty responses

        raises: RuntimeError: On HTTP errors with status code and detail
        """
        url = f"{self._base}{path}"
        logger.debug("jira_api_request", extra={"method": method, "path": path})
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
        }
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            if not response.text:
                logger.debug(
                    "jira_api_success",
                    extra={"method": method, "path": path, "empty_response": True},
                )
                return None
            logger.debug("jira_api_success", extra={"method": method, "path": path})
            return response.json()
        except requests.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            status = exc.response.status_code if exc.response is not None else "unknown"
            logger.error(
                "jira_api_failed",
                extra={"method": method, "path": path, "status_code": status, "detail": detail},
            )
            raise RuntimeError(f"Jira API {method} {path} failed ({status}): {detail}") from exc
        except requests.RequestException as exc:
            detail = str(exc)
            logger.error(
                "jira_api_transport_failed",
                extra={"method": method, "path": path, "detail": detail},
            )
            raise RuntimeError(f"Jira API {method} {path} transport error: {exc}") from exc
        except ValueError as exc:
            # response.json() raises JSONDecodeError (a ValueError subclass) on bad payloads
            detail = str(exc)
            logger.error(
                "jira_api_decode_failed",
                extra={"method": method, "path": path, "detail": detail},
            )
            raise RuntimeError(f"Jira API {method} {path} invalid JSON response: {exc}") from exc

    def validate_project(self, project_key: str) -> None:
        """Validate that Jira project exists and is accessible.

        project_key: Jira project key (e.g., "PROJ")

        raises: RuntimeError: If project is not found or not accessible
        """
        logger.debug("validating_jira_project", extra={"project_key": project_key})
        try:
            self._request("GET", f"/project/{project_key}")
            logger.info("jira_project_validated", extra={"project_key": project_key})
        except RuntimeError as exc:
            logger.error(
                "jira_project_validation_failed",
                extra={"project_key": project_key, "server": self._server},
            )
            raise RuntimeError(
                f"Jira project '{project_key}' was not found on {self._server}. "
                "Check JIRA_PROJECT_KEY in .env (use the prefix from issue keys, e.g. KFLUXUI-123 → KFLUXUI)."
            ) from exc

    def search_issues(
        self,
        jql: str,
        fields: list[str] | None = None,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Search issues using GET /rest/api/3/search/jql (POST is rejected on Red Hat Cloud).

        jql: JQL query string
        fields: List of field names to retrieve (default: standard fields)
        max_results: Maximum number of results to return

        returns: List of issue dictionaries with requested fields
        """
        field_list = fields or [
            "summary",
            "description",
            "status",
            "issuetype",
            "priority",
            "labels",
            "components",
        ]
        collected: list[dict[str, Any]] = []
        next_page_token: str | None = None

        while len(collected) < max_results:
            page_size = min(100, max_results - len(collected))
            query: dict[str, str | int] = {
                "jql": jql,
                "maxResults": page_size,
                "fields": ",".join(field_list),
            }
            if next_page_token:
                query["nextPageToken"] = next_page_token

            path = f"/search/jql?{urlencode(query)}"
            result = self._request("GET", path)
            assert isinstance(result, dict)
            issues = result.get("issues", [])
            collected.extend(issues)
            next_page_token = result.get("nextPageToken")
            if result.get("isLast", False) or not issues or not next_page_token:
                break

        return collected[:max_results]

    def fetch_backlog(self, project_key: str, limit: int = 500) -> list[dict[str, Any]]:
        """Fetch open Jira issues from project backlog.

        project_key: Jira project key
        limit: Maximum number of issues to fetch

        returns: List of simplified issue dictionaries with key, summary, description, etc.
        """
        self.validate_project(project_key)
        days = self._config.jira_backlog_lookback_days
        jql = (
            f"project = {project_key} AND updated >= -{days}d "
            f"AND statusCategory != Done ORDER BY updated DESC"
        )
        logger.debug("fetching_jira_backlog", extra={"project_key": project_key, "limit": limit})
        raw_issues = self.search_issues(jql, max_results=limit)
        logger.debug(
            "fetched_jira_backlog", extra={"project_key": project_key, "count": len(raw_issues)}
        )
        backlog: list[dict[str, Any]] = []
        for issue in raw_issues:
            fields = issue.get("fields", {})
            description = _field_to_text(fields.get("description"))
            if len(description) > 500:
                description = description[:500] + "…"
            backlog.append(
                {
                    "key": issue.get("key"),
                    "summary": fields.get("summary", ""),
                    "description": description,
                    "status": (fields.get("status") or {}).get("name"),
                    "issuetype": (fields.get("issuetype") or {}).get("name"),
                    "priority": (fields.get("priority") or {}).get("name"),
                    "labels": fields.get("labels") or [],
                    "components": [
                        component.get("name")
                        for component in (fields.get("components") or [])
                        if component.get("name")
                    ],
                    "url": f"{self._server}/browse/{issue.get('key')}",
                }
            )
        return backlog

    def fetch_recently_closed(self, project_key: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recently closed/completed Jira issues.

        project_key: Jira project key
        limit: Maximum number of issues to fetch

        returns: List of simplified issue dictionaries for recently completed work
        """
        days = self._config.jira_closed_lookback_days
        jql = (
            f"project = {project_key} AND updated >= -{days}d "
            f"AND statusCategory = Done ORDER BY updated DESC"
        )
        logger.debug("fetching_closed_issues", extra={"project_key": project_key, "limit": limit})
        raw_issues = self.search_issues(
            jql,
            fields=["summary", "status", "labels", "issuetype"],
            max_results=limit,
        )
        closed = [
            {
                "key": issue.get("key"),
                "summary": (issue.get("fields") or {}).get("summary", ""),
                "status": ((issue.get("fields") or {}).get("status") or {}).get("name"),
                "labels": (issue.get("fields") or {}).get("labels") or [],
                "url": f"{self._server}/browse/{issue.get('key')}",
            }
            for issue in raw_issues
        ]
        logger.debug(
            "fetched_closed_issues", extra={"project_key": project_key, "count": len(closed)}
        )
        return closed

    def issue_exists(self, issue_key: str) -> bool:
        """Check if Jira issue exists.

        issue_key: Jira issue key (e.g., "PROJ-123")

        returns: True if issue exists, False if Jira returns 404

        raises: IssueLookupError: On transport errors or non-404 HTTP failures
        """
        logger.debug("checking_issue_exists", extra={"issue_key": issue_key})
        try:
            self._request("GET", f"/issue/{issue_key}?fields=key")
            logger.debug("issue_exists", extra={"issue_key": issue_key, "exists": True})
            return True
        except RuntimeError as exc:
            if _runtime_error_status(exc) == 404:
                logger.debug("issue_not_found", extra={"issue_key": issue_key, "exists": False})
                return False
            logger.error(
                "issue_lookup_failed",
                extra={"issue_key": issue_key, "detail": str(exc)},
            )
            raise IssueLookupError(f"Could not verify Jira issue {issue_key}: {exc}") from exc

    def comment_has_notes_url(self, issue_key: str, notes_url: str) -> bool:
        """Check if issue already has a comment with the given meeting notes URL.

        issue_key: Jira issue key
        notes_url: URL to search for in comments

        returns: True if URL found in any comment, False otherwise
        """
        if not notes_url:
            return False
        logger.debug("checking_duplicate_comment", extra={"issue_key": issue_key})
        result = self._request("GET", f"/issue/{issue_key}/comment")
        assert isinstance(result, dict)
        for comment in result.get("comments", []):
            body = _field_to_text(comment.get("body"))
            if notes_url in body:
                logger.debug("duplicate_comment_found", extra={"issue_key": issue_key})
                return True
        logger.debug("no_duplicate_comment", extra={"issue_key": issue_key})
        return False

    def add_comment(self, issue_key: str, body: str, *, notes_url: str = "") -> dict[str, Any]:
        """Post comment to Jira issue.

        issue_key: Jira issue key
        body: Comment text (plain text or markdown)
        notes_url: optional meeting notes URL appended to the comment footer

        returns: Jira API response with comment details
        """
        logger.debug("posting_comment", extra={"issue_key": issue_key})
        payload = {"body": compose_comment_adf(body, notes_url=notes_url)}
        result = self._request("POST", f"/issue/{issue_key}/comment", payload)
        assert isinstance(result, dict)
        logger.debug("comment_posted", extra={"issue_key": issue_key})
        return result

    def create_issue(
        self,
        fields: dict[str, Any],
        *,
        story_points_suggestion: int | float | str | None = None,
    ) -> dict[str, Any]:
        """Create an issue using the canonical REST payload shape.

        fields: Jira field dictionary (project, summary, description, etc.)
        story_points_suggestion: optional story-point hint rendered in the description

        returns: Jira API response with issue key and details
        """
        summary = fields.get("summary", "(no summary)")
        project = (fields.get("project") or {}).get("key", "unknown")
        logger.debug("creating_jira_issue", extra={"project": project, "summary": summary})
        api_fields = dict(fields)
        if "description" in api_fields:
            api_fields["description"] = compose_issue_description_adf(
                str(api_fields["description"]),
                include_banner=True,
                story_points_suggestion=story_points_suggestion,
            )
        payload = {"fields": api_fields}
        result = self._request("POST", "/issue", payload)
        assert isinstance(result, dict)
        issue_key = result.get("key", "unknown")
        logger.debug("jira_issue_created", extra={"issue_key": issue_key, "project": project})
        return result

    @staticmethod
    def build_create_fields(issue: dict[str, Any], defaults: ScribeConfig) -> dict[str, Any]:
        """Map LLM output to Jira REST API v3 fields.

        issue: LLM-extracted issue dictionary
        defaults: ScribeConfig with default values

        returns: Jira-compatible field structure ready for create_issue()

        raises: ValueError: If required fields (summary/title) are missing
        """
        project_key = issue.get("project_id") or defaults.jira_project_key
        summary = issue.get("summary") or issue.get("title")
        if not summary:
            raise ValueError("Issue summary/title is required")

        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "description": issue.get("description") or issue.get("body") or issue.get("summary"),
            "issuetype": {"name": issue.get("issuetype") or defaults.jira_default_issue_type},
        }

        priority_name = issue.get("priority") or defaults.jira_default_priority
        if priority_name:
            fields["priority"] = {"name": priority_name}

        labels = issue.get("labels") or ["meeting-notes"]
        if labels:
            fields["labels"] = labels

        components = issue.get("components") or []
        if components:
            fields["components"] = [{"name": name} for name in components]

        # Uses the native Jira `parent` field (REST API v3), NOT the deprecated
        # `customfield_10014` (Epic Link) or `customfield_10018` (Parent Link).
        # See: https://community.atlassian.com/forums/Jira-questions/qaq-p/1409874
        parent_issue_id = issue.get("parent_issue_id")
        if parent_issue_id:
            fields["parent"] = {"key": parent_issue_id}

        assignee = issue.get("assignee")
        if assignee:
            fields["assignee"] = (
                {"id": assignee} if assignee.startswith("accountid:") else {"name": assignee}
            )

        return fields


def _field_to_text(value: Any) -> str:
    """Convert Jira field value (plain text, ADF, or other) to plain text string.

        value: Jira field value (string, ADF dict, or other type)

    returns: Plain text representation of the field value
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _adf_to_text(value)
    return str(value)


def _adf_to_text(node: dict[str, Any]) -> str:
    """Convert Atlassian Document Format (ADF) node to plain text.

        node: ADF node dictionary

    returns: Plain text extracted from ADF structure
    """
    node_type = node.get("type")
    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"
    parts: list[str] = []
    for child in node.get("content", []):
        if isinstance(child, dict):
            parts.append(_adf_to_text(child))
    text = "".join(parts)
    if node_type in {"paragraph", "heading", "listItem", "bulletList", "orderedList"}:
        text += "\n"
    return text


def compose_comment_adf(body: str, *, notes_url: str = "") -> dict[str, Any]:
    """Build ADF for a comment body.

        body: the plain-text comment content to convert to ADF
        notes_url: reserved — accepted so callers don't need to change their
            signature if we later decide to append a footer panel (e.g. a link
            back to the source meeting notes). For now the footer was removed
            because it cluttered the comment; keeping the parameter makes it
            trivial to reintroduce without touching every call-site.

    returns: ADF document dict ready to POST to the Jira REST API
    """
    return _text_to_adf(body)


def compose_issue_description_adf(
    description: str,
    *,
    include_banner: bool = False,
    story_points_suggestion: int | float | str | None = None,
    theme: JiraTheme | None = None,
) -> dict[str, Any]:
    """Build ADF for a new issue description with optional banner and story-point advice."""
    active_theme = theme or JiraTheme()
    content: list[dict[str, Any]] = []
    if include_banner:
        content.append(
            _adf_panel(active_theme.panel_warning, agent_banner_text(), active_theme.color_warning)
        )
    content.extend(_text_to_adf(description).get("content", []))
    if story_points_suggestion is not None and str(story_points_suggestion).strip():
        advice = story_point_suggestion_text(story_points_suggestion)
        content.append(_adf_panel(active_theme.panel_info, advice, active_theme.color_info))
    if not content:
        content = _empty_paragraph()
    return {"type": "doc", "version": 1, "content": content}


def _empty_paragraph() -> list[dict[str, Any]]:
    """Return a minimal empty ADF paragraph node list."""
    return [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]


def _adf_panel(panel_type: str, text: str, color: str) -> dict[str, Any]:
    """Render a Jira panel with colored body text (regular weight, not bold)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    panel_content: list[dict[str, Any]] = []
    for line in lines:
        panel_content.append(
            {
                "type": "paragraph",
                "content": [_adf_text(line, color=color)],
            }
        )
    if not panel_content:
        panel_content = _empty_paragraph()
    return {"type": "panel", "attrs": {"panelType": panel_type}, "content": panel_content}


def _adf_text(text: str, *, color: str | None = None, strong: bool = False) -> dict[str, Any]:
    """Build a single ADF text node with optional color and strong marks."""
    node: dict[str, Any] = {"type": "text", "text": text}
    marks: list[dict[str, Any]] = []
    if strong:
        marks.append({"type": "strong"})
    if color:
        marks.append({"type": "textColor", "attrs": {"color": color}})
    if marks:
        node["marks"] = marks
    return node


def _text_to_adf(text: str) -> dict[str, Any]:
    """Convert plain text / lightweight markdown into Atlassian Document Format."""
    content: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        if block.startswith("- ") or block.startswith("* "):
            items = []
            for line in block.splitlines():
                line = line.strip()
                if line.startswith(("- ", "* ")):
                    line = line[2:].strip()
                items.append(
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": _inline_nodes(line),
                            }
                        ],
                    }
                )
            content.append({"type": "bulletList", "content": items})
            continue
        if block.startswith("#"):
            lines = block.splitlines()
            level = len(lines[0]) - len(lines[0].lstrip("#"))
            heading_text = lines[0][level:].strip()
            body_text = "\n".join(lines[1:]).strip()
            if level >= 1 and heading_text:
                content.append(
                    {
                        "type": "heading",
                        "attrs": {"level": min(level, 6)},
                        "content": _inline_nodes(heading_text),
                    }
                )
                if body_text:
                    content.extend(_text_to_adf(body_text).get("content", []))
            elif body_text:
                content.extend(_text_to_adf(body_text).get("content", []))
            else:
                content.append({"type": "paragraph", "content": _inline_nodes(block)})
            continue
        content.append({"type": "paragraph", "content": _inline_nodes(block)})

    if not content:
        content = _empty_paragraph()
    return {"type": "doc", "version": 1, "content": content}


def _inline_nodes(text: str) -> list[dict[str, Any]]:
    """Parse inline markdown elements (bold, links) and convert to ADF inline nodes.

        text: Text with markdown formatting (**bold**, [text](url))

    returns: List of ADF inline node dictionaries
    """
    nodes: list[dict[str, Any]] = []
    pattern = re.compile(r"(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|[^*\[]+)")
    for match in pattern.finditer(text):
        chunk = match.group(0)
        link_match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", chunk)
        if link_match:
            nodes.append(
                {
                    "type": "text",
                    "text": link_match.group(1),
                    "marks": [{"type": "link", "attrs": {"href": link_match.group(2)}}],
                }
            )
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            nodes.append(
                {
                    "type": "text",
                    "text": chunk[2:-2],
                    "marks": [{"type": "strong"}],
                }
            )
            continue
        if chunk:
            nodes.append({"type": "text", "text": chunk})
    return nodes or [{"type": "text", "text": text}]


def fetch_cloud_id(server: str) -> str:
    """Resolve Atlassian cloud ID for scoped service-account API tokens."""
    url = f"{server.rstrip('/')}/_edge/tenant_info"
    response = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    cloud_id = str(payload.get("cloudId", "")).strip()
    if not cloud_id:
        raise RuntimeError(f"Could not resolve Jira cloud ID from {url}")
    return cloud_id


def resolve_jira_api_base(
    server: str,
    *,
    cloud_id: str = "",
    use_service_account: bool = False,
) -> str:
    """Return REST base URL for personal PATs or scoped service-account tokens."""
    resolved_cloud_id = cloud_id.strip()
    if use_service_account and not resolved_cloud_id:
        resolved_cloud_id = fetch_cloud_id(server)
    if resolved_cloud_id:
        return f"https://api.atlassian.com/ex/jira/{resolved_cloud_id}/rest/api/3"
    return f"{server.rstrip('/')}/rest/api/3"


def _basic_auth_header(email: str, token: str) -> str:
    """Generate HTTP Basic Auth header value from email and API token.

        email: Jira user email
        token: Jira API token

    returns: HTTP Basic Auth header value (e.g., "Basic <base64>")
    """
    import base64

    raw = f"{email}:{token}".encode()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"Basic {encoded}"
