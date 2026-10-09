"""Unit tests for Jira REST client (offline, mocked HTTP)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from scribe4jira.jira_client import (
    IssueLookupError,
    JiraClient,
    _field_to_text,
    _text_to_adf,
    compose_issue_description_adf,
    fetch_cloud_id,
    resolve_jira_api_base,
)
from scribe4jira.jira_formatting import JiraTheme


def _mock_requests_response(body: str, *, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.text = body
    response.status_code = status_code
    if status_code < 400 and body:
        response.json.return_value = json.loads(body)
    if status_code >= 400:
        http_error = requests.HTTPError(response=response)
        response.raise_for_status.side_effect = http_error
    else:
        response.raise_for_status.return_value = None
    return response


def _patch_requests_success(payload: dict):
    return patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response(json.dumps(payload)),
    )


def _patch_requests_http_error(status: int, body: str = "not found"):
    return patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response(body, status_code=status),
    )


@pytest.mark.parametrize(
    "site_url,cloud_id,use_service_account,expected",
    [
        pytest.param(
            "https://jira.example.com",
            "",
            False,
            "https://jira.example.com/rest/api/3",
            id="personal_pat",
        ),
        pytest.param(
            "https://jira.example.com",
            "cloud-123",
            True,
            "https://api.atlassian.com/ex/jira/cloud-123/rest/api/3",
            id="service_account",
        ),
    ],
)
def test_resolve_jira_api_base(site_url, cloud_id, use_service_account, expected) -> None:
    """Resolve jira api base."""
    result = resolve_jira_api_base(
        site_url, cloud_id=cloud_id, use_service_account=use_service_account
    )

    assert result == expected


def test_fetch_cloud_id_reads_tenant_info() -> None:
    """Fetch cloud id reads tenant info."""
    payload = json.dumps({"cloudId": "cloud-abc"})
    expected = "cloud-abc"

    with patch(
        "scribe4jira.jira_client.requests.get",
        return_value=_mock_requests_response(payload),
    ):
        result = fetch_cloud_id("https://jira.example.com")

    assert result == expected


def test_jira_client_uses_gateway_base_for_service_account(scribe_config) -> None:
    """Jira client uses gateway base for service account."""
    cfg = scribe_config.__class__(
        **{
            **scribe_config.__dict__,
            "jira_use_service_account": True,
            "jira_cloud_id": "cloud-123",
        }
    )
    expected = "https://api.atlassian.com/ex/jira/cloud-123/rest/api/3"

    client = JiraClient(cfg)

    assert client._base == expected


@pytest.mark.parametrize("status_code", [401, 404, 500])
def test_request_raises_runtime_error_on_http_failure(scribe_config, status_code) -> None:
    """Request raises runtime error on http failure."""
    client = JiraClient(scribe_config)

    with patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response("failure", status_code=status_code),
    ):
        with pytest.raises(RuntimeError, match=f"failed \\({status_code}\\)"):
            client._request("GET", "/project/TEST")


def test_request_raises_runtime_error_on_transport_failure(scribe_config) -> None:
    """Request wraps transport failures as RuntimeError."""
    client = JiraClient(scribe_config)

    with patch(
        "scribe4jira.jira_client.requests.request",
        side_effect=requests.Timeout("timed out"),
    ):
        with pytest.raises(RuntimeError, match="transport error"):
            client._request("GET", "/project/TEST")


def test_request_raises_runtime_error_on_invalid_json(scribe_config) -> None:
    """Request wraps invalid JSON responses as RuntimeError."""
    client = JiraClient(scribe_config)
    response = MagicMock()
    response.text = "not-json"
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("No JSON object could be decoded")

    with patch("scribe4jira.jira_client.requests.request", return_value=response):
        with pytest.raises(RuntimeError, match="invalid JSON response"):
            client._request("GET", "/project/TEST")


def test_request_returns_none_for_empty_body(scribe_config) -> None:
    """Request returns none for empty body."""
    client = JiraClient(scribe_config)

    with patch(
        "scribe4jira.jira_client.requests.request", return_value=_mock_requests_response("")
    ):
        result = client._request("GET", "/project/TEST")

    assert result is None


def test_request_parses_json_body(scribe_config) -> None:
    """Request parses json body."""
    client = JiraClient(scribe_config)
    payload = {"key": "TEST-1"}

    with patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response(json.dumps(payload)),
    ):
        result = client._request("GET", "/issue/TEST-1")

    assert result == payload


def test_request_sends_json_payload(scribe_config) -> None:
    """Request sends json payload."""
    client = JiraClient(scribe_config)

    with patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response(json.dumps({"id": "1"})),
    ) as mock_request:
        client._request("POST", "/issue/TEST-1/comment", {"body": "hello"})

    _, kwargs = mock_request.call_args
    assert kwargs["json"] == {"body": "hello"}
    assert kwargs["method"] == "POST"


def test_validate_project_raises_when_project_missing(scribe_config) -> None:
    """Validate project raises when project missing."""
    client = JiraClient(scribe_config)

    with patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response("not found", status_code=404),
    ):
        with pytest.raises(RuntimeError, match="Jira project 'TEST' was not found"):
            client.validate_project("TEST")


@pytest.mark.parametrize(
    "issue_key,patch_factory,expected",
    [
        pytest.param(
            "TEST-1",
            lambda: _patch_requests_success({"key": "TEST-1"}),
            True,
            id="exists",
        ),
        pytest.param(
            "TEST-404",
            lambda: _patch_requests_http_error(404),
            False,
            id="not_found",
        ),
    ],
)
def test_issue_exists(scribe_config, issue_key, patch_factory, expected) -> None:
    """Issue exists."""
    client = JiraClient(scribe_config)

    with patch_factory():
        result = client.issue_exists(issue_key)

    assert result == expected


@pytest.mark.parametrize(
    "patch_factory",
    [
        pytest.param(lambda: _patch_requests_http_error(500), id="server_error"),
        pytest.param(
            lambda: patch(
                "scribe4jira.jira_client.requests.request",
                side_effect=requests.ConnectionError("connection reset"),
            ),
            id="transport_error",
        ),
    ],
)
def test_issue_exists_raises_on_lookup_failure(scribe_config, patch_factory) -> None:
    """Non-404 failures raise IssueLookupError instead of returning False."""
    client = JiraClient(scribe_config)

    with patch_factory():
        with pytest.raises(IssueLookupError, match="Could not verify Jira issue TEST-1"):
            client.issue_exists("TEST-1")


@pytest.mark.parametrize(
    "notes_url,comment_body,expected",
    [
        pytest.param("", "", False, id="empty_url"),
        pytest.param(
            "https://docs.google.com/document/d/abc/edit",
            "See https://docs.google.com/document/d/abc/edit",
            True,
            id="duplicate_found",
        ),
        pytest.param(
            "https://example.com/doc",
            "No link here",
            False,
            id="not_present",
        ),
    ],
)
def test_comment_has_notes_url(scribe_config, notes_url, comment_body, expected) -> None:
    """Comment has notes url."""
    client = JiraClient(scribe_config)

    with _patch_requests_success({"comments": [{"body": comment_body}]}):
        result = client.comment_has_notes_url("TEST-1", notes_url)

    assert result == expected


def test_build_create_fields_requires_summary(scribe_config) -> None:
    """Build create fields requires summary."""
    with pytest.raises(ValueError, match="summary/title is required"):
        JiraClient.build_create_fields({}, scribe_config)


def test_build_create_fields_uses_config_defaults(scribe_config) -> None:
    """Build create fields uses config defaults."""
    fields = JiraClient.build_create_fields(
        {"summary": "New task", "description": "Details"},
        scribe_config,
    )

    assert fields["project"] == {"key": "TEST"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["priority"] == {"name": "Undefined"}


def test_build_create_fields_maps_parent_issue_id(scribe_config) -> None:
    """Build create fields maps parent_issue_id from LLM output to parent.key."""
    fields = JiraClient.build_create_fields(
        {"summary": "Child task", "parent_issue_id": "TEST-99"},
        scribe_config,
    )

    assert fields["parent"] == {"key": "TEST-99"}


def test_build_create_fields_omits_parent_when_not_set(scribe_config) -> None:
    """Build create fields omits parent field when no parent_issue_id is provided."""
    fields = JiraClient.build_create_fields(
        {"summary": "Top-level task"},
        scribe_config,
    )

    assert "parent" not in fields


def test_build_create_fields_omits_parent_when_null(scribe_config) -> None:
    """Build create fields omits parent field when parent_issue_id is null."""
    fields = JiraClient.build_create_fields(
        {"summary": "Top-level task", "parent_issue_id": None},
        scribe_config,
    )

    assert "parent" not in fields


@pytest.mark.parametrize(
    "assignee,expected_assignee",
    [
        pytest.param("accountid:abc-123", {"id": "accountid:abc-123"}, id="account_id"),
        pytest.param("jdoe", {"name": "jdoe"}, id="name"),
    ],
)
def test_build_create_fields_maps_assignee(scribe_config, assignee, expected_assignee) -> None:
    """Build create fields maps assignee."""
    fields = JiraClient.build_create_fields(
        {"summary": "Assigned task", "assignee": assignee},
        scribe_config,
    )

    assert fields["assignee"] == expected_assignee


@pytest.mark.parametrize(
    "value,expected",
    [
        pytest.param("hello", "hello", id="plain_string"),
        pytest.param(None, "", id="none"),
        pytest.param(42, "42", id="non_string"),
    ],
)
def test_field_to_text(value, expected) -> None:
    """Field to text."""
    result = _field_to_text(value)

    assert result == expected


def test_field_to_text_extracts_text_from_adf() -> None:
    """Field to text extracts text from adf."""
    adf = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}],
    }
    expected = "Hello\n"

    result = _field_to_text(adf)

    assert result == expected


def test_text_to_adf_converts_bold_markdown() -> None:
    """Text to adf converts bold markdown."""
    adf = _text_to_adf("**Important** update")

    paragraph = adf["content"][0]
    text_node = paragraph["content"][0]

    assert text_node["text"] == "Important"
    assert text_node["marks"] == [{"type": "strong"}]


def test_validate_project_succeeds(scribe_config) -> None:
    """Validate project succeeds."""
    client = JiraClient(scribe_config)

    with patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response(json.dumps({"key": "TEST"})),
    ):
        client.validate_project("TEST")


def test_search_issues_collects_paginated_results(scribe_config) -> None:
    """Search issues collects paginated results."""
    client = JiraClient(scribe_config)
    first_page = {
        "issues": [{"key": "TEST-1"}],
        "nextPageToken": "page-2",
        "isLast": False,
    }
    second_page = {
        "issues": [{"key": "TEST-2"}],
        "isLast": True,
    }

    with patch(
        "scribe4jira.jira_client.requests.request",
        side_effect=[
            _mock_requests_response(json.dumps(first_page)),
            _mock_requests_response(json.dumps(second_page)),
        ],
    ):
        issues = client.search_issues("project = TEST", max_results=2)

    assert [issue["key"] for issue in issues] == ["TEST-1", "TEST-2"]


def test_fetch_backlog_truncates_long_descriptions(scribe_config) -> None:
    """Fetch backlog truncates long descriptions."""
    client = JiraClient(scribe_config)
    long_description = "x" * 600
    search_result = {
        "issues": [
            {
                "key": "TEST-1",
                "fields": {
                    "summary": "Task",
                    "description": long_description,
                    "status": {"name": "Open"},
                    "issuetype": {"name": "Task"},
                    "priority": {"name": "High"},
                    "labels": [],
                    "components": [{"name": "Core"}],
                },
            }
        ],
        "isLast": True,
    }

    with patch(
        "scribe4jira.jira_client.requests.request",
        side_effect=[
            _mock_requests_response(json.dumps({"key": "TEST"})),
            _mock_requests_response(json.dumps(search_result)),
        ],
    ):
        backlog = client.fetch_backlog("TEST", limit=1)

    assert backlog[0]["description"].endswith("…")
    assert backlog[0]["components"] == ["Core"]


def test_fetch_recently_closed_maps_issue_fields(scribe_config) -> None:
    """Fetch recently closed maps issue fields."""
    client = JiraClient(scribe_config)
    search_result = {
        "issues": [
            {
                "key": "TEST-9",
                "fields": {
                    "summary": "Done task",
                    "status": {"name": "Closed"},
                    "labels": ["done"],
                },
            }
        ],
        "isLast": True,
    }

    with patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response(json.dumps(search_result)),
    ):
        closed = client.fetch_recently_closed("TEST", limit=1)

    assert closed[0]["key"] == "TEST-9"
    assert closed[0]["status"] == "Closed"


def test_add_comment_posts_adf_payload(scribe_config) -> None:
    """Add comment posts adf payload."""
    client = JiraClient(scribe_config)

    with patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response(json.dumps({"id": "10001"})),
    ):
        result = client.add_comment("TEST-1", "Update")

    assert result["id"] == "10001"


def test_create_issue_posts_fields_payload(scribe_config) -> None:
    """Create issue posts fields payload."""
    client = JiraClient(scribe_config)
    fields = {
        "project": {"key": "TEST"},
        "summary": "New task",
        "description": "Details",
        "issuetype": {"name": "Task"},
    }

    with patch(
        "scribe4jira.jira_client.requests.request",
        return_value=_mock_requests_response(json.dumps({"key": "TEST-10"})),
    ):
        result = client.create_issue(fields)

    assert result["key"] == "TEST-10"


def test_build_create_fields_includes_components(scribe_config) -> None:
    """Build create fields includes components."""
    fields = JiraClient.build_create_fields(
        {"summary": "Task", "components": ["Core", "Docs"]},
        scribe_config,
    )

    assert fields["components"] == [{"name": "Core"}, {"name": "Docs"}]


def test_adf_to_text_includes_hard_break() -> None:
    """Adf to text includes hard break."""
    from scribe4jira.jira_client import _adf_to_text

    assert _adf_to_text({"type": "hardBreak"}) == "\n"


@pytest.mark.parametrize(
    "markdown,expected_type",
    [
        pytest.param("- first\n- second", "bulletList", id="bullet_list"),
        pytest.param("# Title", "heading", id="heading"),
    ],
)
def test_text_to_adf_converts_block_markdown(markdown, expected_type) -> None:
    """Text to adf converts block markdown."""
    result = _text_to_adf(markdown)

    assert result["content"][0]["type"] == expected_type


def test_text_to_adf_keeps_body_text_outside_heading() -> None:
    """Text to adf keeps body text outside heading."""
    adf = _text_to_adf("## Problem\nWhat needs to be built.")

    assert adf["content"][0]["type"] == "heading"
    assert adf["content"][0]["content"][0]["text"] == "Problem"
    assert adf["content"][1]["type"] == "paragraph"
    assert adf["content"][1]["content"][0]["text"] == "What needs to be built."


def test_compose_issue_description_adf_includes_warning_banner() -> None:
    """Compose issue description adf includes warning banner."""
    from scribe4jira.jira_formatting import THEME_COLOR_WARNING

    adf = compose_issue_description_adf("## Problem\nDetails", include_banner=True)

    banner = adf["content"][0]
    assert banner["type"] == "panel"
    assert banner["attrs"]["panelType"] == "warning"
    banner_color = banner["content"][0]["content"][0]["marks"][0]["attrs"]["color"]
    assert banner_color == THEME_COLOR_WARNING
    assert adf["content"][1]["type"] == "heading"


def test_compose_issue_description_adf_warning_banner_uses_theme_override() -> None:
    """Compose issue description adf warning banner uses theme override."""
    custom_theme = JiraTheme(color_warning="#AABB00", panel_warning="warning")

    adf = compose_issue_description_adf("Body", include_banner=True, theme=custom_theme)

    banner = adf["content"][0]
    assert banner["attrs"]["panelType"] == "warning"
    assert banner["content"][0]["content"][0]["marks"][0]["attrs"]["color"] == "#AABB00"


def test_compose_issue_description_adf_appends_story_point_panel() -> None:
    """Compose issue description adf appends story point panel."""
    adf = compose_issue_description_adf("Body", story_points_suggestion=3)

    panel = adf["content"][-1]
    assert panel["type"] == "panel"
    assert panel["attrs"]["panelType"] == "info"
    assert "3" in panel["content"][0]["content"][0]["text"]


def test_text_to_adf_converts_markdown_link() -> None:
    """Text to adf converts markdown link."""
    adf = _text_to_adf("[Docs](https://example.com)")

    text_node = adf["content"][0]["content"][0]

    assert text_node["marks"][0]["attrs"]["href"] == "https://example.com"


def test_text_to_adf_returns_empty_paragraph_for_blank_input() -> None:
    """Text to adf returns empty paragraph for blank input."""
    adf = _text_to_adf("   ")

    assert adf["content"] == [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]
