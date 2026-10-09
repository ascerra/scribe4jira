"""Unit tests for environment configuration."""

from __future__ import annotations

import os

import pytest
from conftest import REQUIRED_ENV, apply_required_env

from scribe4jira.config import ScribeConfig


@pytest.mark.parametrize(
    ("attribute", "expected_value"),
    [
        ("jira_server", "https://jira.example.com"),
        ("jira_email", "scribe@example.com"),
        ("jira_project_key", "TEST"),
        ("dry_run", True),
    ],
)
def test_from_env_loads_default_values(scribe_env, attribute, expected_value) -> None:
    """From env loads default values."""
    cfg = ScribeConfig.from_env(load_dotenv=False)

    assert getattr(cfg, attribute) == expected_value


def test_from_env_uses_workspace_dir(scribe_env) -> None:
    """From env uses workspace dir."""
    cfg = ScribeConfig.from_env(load_dotenv=False)

    assert cfg.workspace_dir == scribe_env


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("false", False),
        ("0", False),
        ("no", False),
        ("true", True),
        ("yes", True),
        ("1", True),
    ],
)
def test_from_env_parses_dry_run_boolean(scribe_env, monkeypatch, env_value, expected) -> None:
    """From env parses dry run boolean."""
    monkeypatch.setenv("SCRIBE_DRY_RUN", env_value)

    cfg = ScribeConfig.from_env(load_dotenv=False)

    assert cfg.dry_run is expected


def test_from_env_respects_min_confidence_override(scribe_env, monkeypatch) -> None:
    """From env respects min confidence override."""
    monkeypatch.setenv("SCRIBE_MIN_CONFIDENCE", "0.75")

    cfg = ScribeConfig.from_env(load_dotenv=False)

    assert cfg.min_confidence == 0.75


@pytest.mark.parametrize("missing_var", tuple(REQUIRED_ENV))
def test_from_env_raises_when_required_value_missing(monkeypatch, missing_var) -> None:
    """From env raises when required value missing."""
    monkeypatch.setattr("scribe4jira.config._load_dotenv_if_present", lambda: None)
    apply_required_env(monkeypatch, exclude=missing_var)

    with pytest.raises(RuntimeError, match=missing_var):
        ScribeConfig.from_env(load_dotenv=False)


def test_ensure_workspace_creates_directories(scribe_env) -> None:
    """Ensure workspace creates directories."""
    cfg = ScribeConfig.from_env(load_dotenv=False)
    cfg.ensure_workspace()

    assert cfg.workspace_dir.is_dir()
    assert cfg.notes_dir.is_dir()


def test_first_non_empty_returns_first_set_value(monkeypatch) -> None:
    """First non empty returns first set value."""
    from scribe4jira.config import _first_non_empty

    monkeypatch.setenv("PRIMARY", "")
    monkeypatch.setenv("SECONDARY", "value")

    assert _first_non_empty("PRIMARY", "SECONDARY") == "value"


def test_resolve_path_returns_absolute_path(tmp_path) -> None:
    """Resolve path returns absolute path."""
    from scribe4jira.config import _resolve_path

    absolute = tmp_path / "creds.json"
    absolute.write_text("{}", encoding="utf-8")

    assert _resolve_path(str(absolute)) == str(absolute.resolve())


def test_resolve_path_finds_relative_file(scribe_env, tmp_path, monkeypatch) -> None:
    """Resolve path finds relative file."""
    from scribe4jira.config import _resolve_path

    creds = tmp_path / "creds.json"
    creds.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _resolve_path("creds.json") == str(creds.resolve())


def test_load_dotenv_if_present_sets_missing_keys(tmp_path, monkeypatch) -> None:
    """Load dotenv if present sets missing keys."""
    import scribe4jira.config as config_module
    from scribe4jira.config import _load_dotenv_if_present

    env_file = tmp_path / ".env"
    env_file.write_text("SCRIBE_TEST_DOTENV=loaded\n# comment line\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "__file__", str(tmp_path / "config.py"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCRIBE_TEST_DOTENV", raising=False)

    _load_dotenv_if_present()

    assert os.environ["SCRIBE_TEST_DOTENV"] == "loaded"
    monkeypatch.delenv("SCRIBE_TEST_DOTENV", raising=False)


def test_optional_bool_uses_default_when_unset(monkeypatch) -> None:
    """Optional bool uses default when unset."""
    from scribe4jira.config import _optional_bool

    monkeypatch.delenv("UNSET_BOOL", raising=False)

    assert _optional_bool("UNSET_BOOL", True) is True


def test_optional_int_uses_default_when_unset(monkeypatch) -> None:
    """Optional int uses default when unset."""
    from scribe4jira.config import _optional_int

    monkeypatch.delenv("UNSET_INT", raising=False)

    assert _optional_int("UNSET_INT", 42) == 42
