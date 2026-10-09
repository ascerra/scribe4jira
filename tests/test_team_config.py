"""Unit tests for team_config: unified config loader, SecretResolver."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from team_config import (
    PrefixedEnvResolver,
    load_for_team,
    validate_config,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = textwrap.dedent("""\
    general_config:
      server: "https://jira.example.com"

    teams:
      testteam:
        jira_email: "bot@example.com"
        jira_project_key: "TEST"
""")

FULL_CONFIG = textwrap.dedent("""\
    general_config:
      server: "https://jira.example.com"
      default_issue_type: "Task"
      default_priority: "Undefined"
      backlog_lookback_days: 30
      closed_lookback_days: 30
      use_service_account: false
      cloud_id: "cloud-123"
      notes_source: "drive"
      search_query: "standup"
      lookback_hours: 4
      min_confidence: 0.7
      mode: "comments_only"
      dry_run: false
      color_info: "#445566"
      color_warning: "#778899"

    teams:
      konflux:
        jira_email: "bot@example.com"
        jira_project_key: "TEST"
        env_suffix: "EXAMPLE"
""")


def write_config(tmp_path: Path, content: str, name: str = "config.yaml") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


_FAKE_GCP_JSON = '{"type": "service_account"}'


def _minimal_secrets(monkeypatch) -> None:
    """Satisfy the minimum required secrets for MINIMAL_CONFIG (team=testteam, suffix=TESTTEAM)."""
    monkeypatch.setenv("JIRA_API_TOKEN_TESTTEAM", "tok")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_TESTTEAM", _FAKE_GCP_JSON)


# ---------------------------------------------------------------------------
# PrefixedEnvResolver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_suffix", "expected_suffix"),
    [
        ("myteam", "_MYTEAM"),
        ("MYTEAM", "_MYTEAM"),
        ("MyTeam", "_MYTEAM"),
    ],
)
def test_resolver_suffix_is_always_uppercased(input_suffix, expected_suffix) -> None:
    """Resolver suffix is always uppercased."""
    resolver = PrefixedEnvResolver(input_suffix)

    result = resolver.suffix

    assert result == expected_suffix


@pytest.mark.parametrize(
    ("prefixed_val", "expected"),
    [
        ("prefixed-key", "prefixed-key"),  # prefixed present → use it
        ("", "shared-key"),  # prefixed absent → fallback to shared
    ],
)
def test_resolver_shared_get_resolution(monkeypatch, prefixed_val, expected) -> None:
    """Resolver shared get resolution."""
    monkeypatch.setenv("SCRIBE_SLACK_WEBHOOK_URL_MYTEAM", prefixed_val)
    monkeypatch.setenv("SCRIBE_SLACK_WEBHOOK_URL", "shared-key")
    resolver = PrefixedEnvResolver("myteam")

    result = resolver.get("SCRIBE_SLACK_WEBHOOK_URL")

    assert result == expected


def test_resolver_raises_when_both_shared_and_prefixed_absent(monkeypatch) -> None:
    """Resolver raises when both shared and prefixed absent."""
    monkeypatch.delenv("SCRIBE_SLACK_WEBHOOK_URL_MYTEAM", raising=False)
    monkeypatch.delenv("SCRIBE_SLACK_WEBHOOK_URL", raising=False)
    resolver = PrefixedEnvResolver("myteam")

    with pytest.raises(RuntimeError, match="SCRIBE_SLACK_WEBHOOK_URL_MYTEAM"):
        resolver.get("SCRIBE_SLACK_WEBHOOK_URL")


@pytest.mark.parametrize(
    ("prefixed_val", "shared_val", "raises"),
    [
        ("team-token", "", False),  # strict: prefixed present → OK
        ("", "shared-tok", True),  # strict: prefixed absent → always raises
        ("", "", True),  # strict: both absent → raises
    ],
)
def test_resolver_strict_get(monkeypatch, prefixed_val, shared_val, raises) -> None:
    """Resolver strict get."""
    monkeypatch.setenv("JIRA_API_TOKEN_MYTEAM", prefixed_val)
    monkeypatch.setenv("JIRA_API_TOKEN", shared_val)
    resolver = PrefixedEnvResolver("myteam")

    if raises:
        with pytest.raises(RuntimeError, match="JIRA_API_TOKEN_MYTEAM"):
            resolver.get("JIRA_API_TOKEN", strict=True)
    else:
        result = resolver.get("JIRA_API_TOKEN", strict=True)

        assert result == prefixed_val


@pytest.mark.parametrize(
    ("prefixed_val", "default", "expected"),
    [
        ("hook-url", "fallback", "hook-url"),  # present → value returned
        ("", "fallback", "fallback"),  # absent → default returned
    ],
)
def test_resolver_get_optional(monkeypatch, prefixed_val, default, expected) -> None:
    """Resolver get optional."""
    monkeypatch.setenv("SCRIBE_SLACK_WEBHOOK_URL_MYTEAM", prefixed_val)
    monkeypatch.delenv("SCRIBE_SLACK_WEBHOOK_URL", raising=False)
    resolver = PrefixedEnvResolver("myteam")

    result = resolver.get_optional("SCRIBE_SLACK_WEBHOOK_URL", default)

    assert result == expected


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


def test_validate_config_passes_for_minimal_valid_config(tmp_path) -> None:
    """Validate config passes for minimal valid config."""
    path = write_config(tmp_path, MINIMAL_CONFIG)

    validate_config(path)  # must not raise


def test_validate_config_passes_for_full_config(tmp_path) -> None:
    """Validate config passes for full config."""
    path = write_config(tmp_path, FULL_CONFIG)

    validate_config(path)  # must not raise


@pytest.mark.parametrize(
    ("yaml_content", "expected_error_fragment"),
    [
        # No general_config section at all
        (
            "teams:\n  t:\n    jira_email: x\n    jira_project_key: Y\n",
            "general_config",
        ),
        # general_config present but server field missing
        (
            "general_config:\n  model: x\nteams:\n  t:\n    jira_email: x\n    jira_project_key: Y\n",
            "server",
        ),
        # No teams section at all
        (
            "general_config:\n  server: x\n",
            "teams",
        ),
        # teams present but team block is empty (no fields)
        (
            "general_config:\n  server: x\nteams:\n  t:\n",
            "teams",
        ),
        # Team missing jira_email
        (
            "general_config:\n  server: x\nteams:\n  t:\n    jira_project_key: Y\n",
            "jira_email",
        ),
        # Team missing jira_project_key
        (
            "general_config:\n  server: x\nteams:\n  t:\n    jira_email: x\n",
            "jira_project_key",
        ),
    ],
)
def test_validate_config_raises_for_missing_required_field(
    tmp_path, yaml_content, expected_error_fragment
) -> None:
    """Validate config raises for missing required field."""
    path = write_config(tmp_path, yaml_content)

    with pytest.raises(ValueError, match=expected_error_fragment):
        validate_config(path)


# ---------------------------------------------------------------------------
# load_for_team -- non-secret values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_key", "expected"),
    [
        ("JIRA_SERVER", "https://jira.example.com"),
        ("JIRA_EMAIL", "bot@example.com"),
        ("JIRA_PROJECT_KEY", "TEST"),
    ],
)
def test_load_for_team_non_secrets_from_general_and_team(
    tmp_path, monkeypatch, env_key, expected
) -> None:
    """Load for team non secrets from general and team."""
    _minimal_secrets(monkeypatch)
    monkeypatch.delenv(env_key, raising=False)
    path = write_config(tmp_path, MINIMAL_CONFIG)

    load_for_team("testteam", path)

    assert os.environ[env_key] == expected


def test_load_for_team_dry_run_from_config(tmp_path, monkeypatch) -> None:
    """YAML bool ``false`` becomes str 'False'; ScribeConfig._optional_bool() lowercases it."""
    _minimal_secrets(monkeypatch)
    monkeypatch.delenv("SCRIBE_DRY_RUN", raising=False)
    # FULL_CONFIG has dry_run: false
    monkeypatch.setenv("JIRA_API_TOKEN_EXAMPLE", "tok")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_EXAMPLE", _FAKE_GCP_JSON)
    path = write_config(tmp_path, FULL_CONFIG)

    load_for_team("konflux", path)

    # str(False) == "False"; ScribeConfig._optional_bool() lowercases before parsing
    assert os.environ["SCRIBE_DRY_RUN"].lower() == "false"


@pytest.mark.parametrize(
    ("env_key", "expected"),
    [
        ("SCRIBE_THEME_COLOR_INFO", "#445566"),
        ("SCRIBE_THEME_COLOR_WARNING", "#778899"),
    ],
)
def test_load_for_team_theme_colors_from_general_config(
    tmp_path, monkeypatch, env_key, expected
) -> None:
    """Load for team theme colors from general config."""
    monkeypatch.setenv("JIRA_API_TOKEN_EXAMPLE", "tok")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_EXAMPLE", _FAKE_GCP_JSON)
    monkeypatch.delenv(env_key, raising=False)
    path = write_config(tmp_path, FULL_CONFIG)

    load_for_team("konflux", path)

    assert os.environ[env_key] == expected


def test_load_for_team_team_values_override_general(tmp_path, monkeypatch) -> None:
    """A key present in both general_config and the team block → team value wins."""
    _minimal_secrets(monkeypatch)
    config = textwrap.dedent("""\
        general_config:
          server: "https://general.example.com"
          search_query: "general query"

        teams:
          testteam:
            jira_email: "bot@example.com"
            jira_project_key: "TEST"
            search_query: "team query"
    """)
    monkeypatch.delenv("SCRIBE_SEARCH_QUERY", raising=False)
    path = write_config(tmp_path, config)

    load_for_team("testteam", path)

    assert os.environ["SCRIBE_SEARCH_QUERY"] == "team query"


# ---------------------------------------------------------------------------
# load_for_team -- setdefault behaviour (existing env wins)
# ---------------------------------------------------------------------------


def test_load_for_team_preserves_existing_non_secret(tmp_path, monkeypatch) -> None:
    """Load for team preserves existing non secret."""
    _minimal_secrets(monkeypatch)
    monkeypatch.setenv("JIRA_PROJECT_KEY", "OVERRIDE")
    path = write_config(tmp_path, MINIMAL_CONFIG)

    load_for_team("testteam", path)

    assert os.environ["JIRA_PROJECT_KEY"] == "OVERRIDE"


def test_load_for_team_injects_absent_non_secret(tmp_path, monkeypatch) -> None:
    """Load for team injects absent non secret."""
    _minimal_secrets(monkeypatch)
    monkeypatch.delenv("JIRA_PROJECT_KEY", raising=False)
    path = write_config(tmp_path, MINIMAL_CONFIG)

    load_for_team("testteam", path)

    assert os.environ["JIRA_PROJECT_KEY"] == "TEST"


# ---------------------------------------------------------------------------
# load_for_team -- secret resolution
# ---------------------------------------------------------------------------


def test_load_for_team_jira_token_uses_prefixed_var(tmp_path, monkeypatch) -> None:
    """Load for team jira token uses prefixed var."""
    monkeypatch.setenv("JIRA_API_TOKEN_TESTTEAM", "team-token")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_TESTTEAM", _FAKE_GCP_JSON)
    path = write_config(tmp_path, MINIMAL_CONFIG)

    load_for_team("testteam", path)

    assert os.environ["JIRA_API_TOKEN"] == "team-token"


def test_load_for_team_raises_when_jira_token_missing(tmp_path, monkeypatch) -> None:
    """Load for team raises when jira token missing."""
    monkeypatch.delenv("JIRA_API_TOKEN_TESTTEAM", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    path = write_config(tmp_path, MINIMAL_CONFIG)

    with pytest.raises(RuntimeError, match="JIRA_API_TOKEN_TESTTEAM"):
        load_for_team("testteam", path)


def test_load_for_team_env_suffix_override(tmp_path, monkeypatch) -> None:
    """Load for team env suffix override."""
    config = textwrap.dedent("""\
        general_config:
          server: "https://jira.example.com"

        teams:
          testteam:
            jira_email: "bot@example.com"
            jira_project_key: "TEST"
            env_suffix: CUSTOM
    """)
    monkeypatch.setenv("JIRA_API_TOKEN_CUSTOM", "custom-tok")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_CUSTOM", _FAKE_GCP_JSON)
    path = write_config(tmp_path, config)

    load_for_team("testteam", path)

    assert os.environ["JIRA_API_TOKEN"] == "custom-tok"


@pytest.mark.parametrize(
    ("slack_val", "expected_in_env"),
    [
        ("https://hooks.slack.com/abc", True),
        ("", False),
    ],
)
def test_load_for_team_slack_webhook_presence(
    tmp_path, monkeypatch, slack_val, expected_in_env
) -> None:
    """Load for team slack webhook presence."""
    _minimal_secrets(monkeypatch)
    monkeypatch.setenv("SCRIBE_SLACK_WEBHOOK_URL_TESTTEAM", slack_val)
    monkeypatch.delenv("SCRIBE_SLACK_WEBHOOK_URL", raising=False)
    path = write_config(tmp_path, MINIMAL_CONFIG)

    load_for_team("testteam", path)

    result = "SCRIBE_SLACK_WEBHOOK_URL" in os.environ

    assert result == expected_in_env


def test_load_for_team_gcp_credentials_written_to_file(tmp_path, monkeypatch) -> None:
    """load_for_team writes GCP JSON to a temp file and sets GOOGLE_APPLICATION_CREDENTIALS."""
    _minimal_secrets(monkeypatch)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    path = write_config(tmp_path, MINIMAL_CONFIG)

    load_for_team("testteam", path)

    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    assert cred_path, "GOOGLE_APPLICATION_CREDENTIALS should be set after load_for_team"
    assert Path(cred_path).exists(), "GCP credential file must exist on disk"
    assert Path(cred_path).read_text(encoding="utf-8") == _FAKE_GCP_JSON
    assert "GOOGLE_APPLICATION_CREDENTIALS_JSON_TESTTEAM" not in os.environ
    assert "GOOGLE_APPLICATION_CREDENTIALS_JSON" not in os.environ


def test_load_for_team_gcp_credentials_missing_raises(tmp_path, monkeypatch) -> None:
    """load_for_team raises when GOOGLE_APPLICATION_CREDENTIALS_JSON_<SUFFIX> is absent."""
    monkeypatch.setenv("JIRA_API_TOKEN_TESTTEAM", "tok")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_TESTTEAM", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", raising=False)
    path = write_config(tmp_path, MINIMAL_CONFIG)

    with pytest.raises(RuntimeError, match="GOOGLE_APPLICATION_CREDENTIALS_JSON_TESTTEAM"):
        load_for_team("testteam", path)


def test_load_for_team_raises_for_unknown_team(tmp_path, monkeypatch) -> None:
    """Load for team raises for unknown team."""
    _minimal_secrets(monkeypatch)
    path = write_config(tmp_path, MINIMAL_CONFIG)

    with pytest.raises(ValueError, match="no-such-team"):
        load_for_team("no-such-team", path)


# ---------------------------------------------------------------------------
# Integration: load_for_team → ScribeConfig.from_env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cfg_attribute", "expected"),
    [
        ("jira_server", "https://jira.example.com"),
        ("jira_email", "bot@example.com"),
        ("jira_project_key", "TEST"),
        ("jira_token", "test-jira-tok"),
    ],
)
def test_scribe_config_built_from_unified_config(
    tmp_path, monkeypatch, cfg_attribute, expected
) -> None:
    """load_for_team followed by ScribeConfig.from_env reads all config values."""
    from scribe4jira.config import ScribeConfig

    monkeypatch.setenv("JIRA_API_TOKEN_TESTTEAM", "test-jira-tok")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_TESTTEAM", _FAKE_GCP_JSON)
    monkeypatch.setenv("SCRIBE_WORKSPACE_DIR", str(tmp_path / "ws"))
    for key in ("JIRA_SERVER", "JIRA_EMAIL", "JIRA_PROJECT_KEY", "SCRIBE_DRY_RUN"):
        monkeypatch.delenv(key, raising=False)

    path = write_config(tmp_path, MINIMAL_CONFIG)
    load_for_team("testteam", path)

    cfg = ScribeConfig.from_env(load_dotenv=False)

    result = getattr(cfg, cfg_attribute)

    assert result == expected


@pytest.mark.parametrize(
    ("cfg_attribute", "expected"),
    [
        ("theme_color_info", "#445566"),
        ("theme_color_warning", "#778899"),
    ],
)
def test_scribe_config_theme_colors_from_unified_config(
    tmp_path, monkeypatch, cfg_attribute, expected
) -> None:
    """Scribe config theme colors from unified config."""
    from scribe4jira.config import ScribeConfig

    monkeypatch.setenv("JIRA_API_TOKEN_EXAMPLE", "tok")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_EXAMPLE", _FAKE_GCP_JSON)
    monkeypatch.setenv("SCRIBE_WORKSPACE_DIR", str(tmp_path / "ws"))
    for key in (
        "JIRA_SERVER",
        "JIRA_EMAIL",
        "JIRA_PROJECT_KEY",
        "SCRIBE_DRY_RUN",
        "SCRIBE_THEME_COLOR_INFO",
        "SCRIBE_THEME_COLOR_WARNING",
    ):
        monkeypatch.delenv(key, raising=False)

    path = write_config(tmp_path, FULL_CONFIG)
    load_for_team("konflux", path)

    cfg = ScribeConfig.from_env(load_dotenv=False)

    result = getattr(cfg, cfg_attribute)

    assert result == expected
