"""Load unified config.yaml and resolve team-prefixed secrets.

Entry point
-----------
``load_for_team(team_name)`` — call once at process start.  Reads
``configs/config.yaml``, merges team overrides on top of general defaults,
then injects the result into ``os.environ`` so ``ScribeConfig.from_env()``
picks everything up without any changes.

Secret-resolution modes
-----------------------
strict  -- only ``{KEY}_{SUFFIX}`` is accepted; no fallback to unprefixed ``{KEY}``.
           Used for ``JIRA_API_TOKEN`` and ``GOOGLE_APPLICATION_CREDENTIALS_JSON``:
           every team must have its own credentials.

shared  -- tries ``{KEY}_{SUFFIX}`` first, then falls back to unprefixed ``{KEY}``.
           Used for shared credentials where a single key is acceptable.

The suffix defaults to ``team_name.upper()`` but can be overridden via
``teams.<name>.env_suffix`` in the YAML.

Vault migration
---------------
Set ``SCRIBE_SECRET_BACKEND=vault`` to swap the resolver backend from
environment variables to HashiCorp Vault (not yet implemented — raises
``NotImplementedError`` with instructions).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol

from scribe4jira.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_CONFIG_PATH = "configs/config.yaml"


# ---------------------------------------------------------------------------
# SecretResolver protocol + implementations
# ---------------------------------------------------------------------------


class SecretResolver(Protocol):
    """Interface for resolving secrets. Implement for different backends."""

    def get(self, key: str, *, strict: bool = False) -> str: ...
    def get_optional(self, key: str, default: str = "", *, strict: bool = False) -> str: ...


class PrefixedEnvResolver:
    """Resolve secrets from env vars using the ``{KEY}_{SUFFIX}`` convention.

    Args:
        suffix: Team-specific suffix appended to each key, e.g. ``"EXAMPLE"``.
                Stored internally as ``_EXAMPLE`` (leading underscore included).
    """

    def __init__(self, suffix: str) -> None:
        self.suffix = f"_{suffix.upper()}"

    def get(self, key: str, *, strict: bool = False) -> str:
        """Resolve a required secret.

        Args:
            key:    Canonical env var name, e.g. ``"JIRA_API_TOKEN"``.
            strict: When ``True``, only the prefixed variable is accepted.
                    When ``False`` (default), falls back to the unprefixed var.

        Raises:
            RuntimeError: If the secret cannot be resolved.
        """
        prefixed = f"{key}{self.suffix}"
        value = os.environ.get(prefixed, "").strip()
        if not value and not strict:
            value = os.environ.get(key, "").strip()
        if not value:
            if strict:
                raise RuntimeError(
                    f"Required team-specific secret {prefixed} is not set "
                    f"(no fallback allowed for this key)"
                )
            raise RuntimeError(f"Required secret not found: tried {prefixed} and {key}")
        return value

    def get_optional(self, key: str, default: str = "", *, strict: bool = False) -> str:
        """Resolve an optional secret, returning ``default`` if absent."""
        prefixed = f"{key}{self.suffix}"
        value = os.environ.get(prefixed, "").strip()
        if not value and not strict:
            value = os.environ.get(key, "").strip()
        return value or default


class VaultResolver:
    """Resolve secrets from HashiCorp Vault (future implementation).

    To enable: set ``SCRIBE_SECRET_BACKEND=vault`` and implement this class.

    Expected env vars::

        VAULT_ADDR   -- e.g. "https://vault.internal:8200"
        VAULT_TOKEN  -- Vault token with read access to the team path

    Vault path convention::

        secret/data/scribe/<team>/<SECRET_KEY>

    Replace the ``get``/``get_optional`` stubs with actual hvac calls once
    the enterprise Vault service is provisioned.
    """

    def __init__(self, team: str) -> None:
        self.team = team
        self.path = f"secret/data/scribe/{team}"

    def get(self, key: str, *, strict: bool = False) -> str:  # noqa: ARG002
        raise NotImplementedError(
            f"VaultResolver.get({key!r}) is not yet implemented. "
            "Install hvac, authenticate, and read from Vault path "
            f"{self.path}/{key}."
        )

    def get_optional(self, key: str, default: str = "", *, strict: bool = False) -> str:  # noqa: ARG002
        try:
            return self.get(key, strict=strict)
        except NotImplementedError:
            return default


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_yaml(yaml_path: str | Path) -> dict:
    """Load and parse a YAML config file, returning an empty dict for empty files."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "pyyaml is required. Ensure pyyaml is listed in requirements.txt."
        ) from exc
    return yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}


def _build_resolver(suffix: str) -> SecretResolver:
    """Return the active SecretResolver based on ``SCRIBE_SECRET_BACKEND``."""
    backend = os.environ.get("SCRIBE_SECRET_BACKEND", "env").lower()
    if backend == "vault":
        raise NotImplementedError(
            f"SCRIBE_SECRET_BACKEND=vault is not yet implemented. "
            f"Complete VaultResolver and wire it here (team={suffix.lower()!r}). "
            "Required env: VAULT_ADDR, VAULT_TOKEN."
        )
    return PrefixedEnvResolver(suffix)


def _write_credential_json(json_content: str) -> str:
    """Write GCP service account JSON to a temporary file and return its path.

        json_content: JSON string content of the service account key

    returns: Absolute path to the written temp file
    """
    fd, path = tempfile.mkstemp(suffix=".json", prefix="scribe-gcp-sa-")
    try:
        os.write(fd, json_content.encode("utf-8"))
    finally:
        os.close(fd)
    return path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """Validate the unified config.yaml structure.

    Raises:
        ValueError: Listing all validation errors found.
    """
    data = _load_yaml(config_path)
    errors: list[str] = []

    general = data.get("general_config")
    if not general:
        errors.append("missing required section: general_config")
    elif not general.get("server"):
        errors.append("missing required field: general_config.server")

    teams = data.get("teams")
    if not teams or not isinstance(teams, dict):
        errors.append("missing required section: teams (must be a non-empty mapping)")
    else:
        for team_name, team in teams.items():
            if not team or not isinstance(team, dict):
                errors.append(f"teams.{team_name}: must be a mapping")
                continue
            for field in ("jira_email", "jira_project_key"):
                if not team.get(field):
                    errors.append(f"teams.{team_name}: missing required field: {field}")

    if errors:
        raise ValueError(f"Invalid config {config_path}:\n" + "\n".join(f"  - {e}" for e in errors))


def load_for_team(
    team_name: str,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> None:
    """Load unified config, merge team overrides, inject into ``os.environ``.

    Non-secret config uses ``setdefault`` — existing env vars (e.g. CI/CD
    job-level overrides) always win over YAML values.
    Secrets are written directly so ``ScribeConfig.from_env()`` finds them
    under their canonical names (e.g. ``JIRA_API_TOKEN``).

    Args:
        team_name:   Team key as it appears under ``teams:`` in the YAML.
        config_path: Path to the unified config file. Defaults to
                     ``configs/config.yaml``.

    Raises:
        ValueError:   If the team is not found in the config.
        RuntimeError: If a required secret (``JIRA_API_TOKEN_<SUFFIX>`` or
                      ``GOOGLE_APPLICATION_CREDENTIALS_JSON_<SUFFIX>``) cannot be resolved.
    """
    data = _load_yaml(config_path)
    general: dict = data.get("general_config", {})
    teams: dict = data.get("teams", {})

    if team_name not in teams:
        raise ValueError(
            f"Team '{team_name}' not found in {config_path}. Available teams: {sorted(teams)}"
        )

    team = teams[team_name]
    # Team keys override general defaults
    merged = {**general, **team}

    non_secrets: dict[str, object] = {
        "JIRA_SERVER": merged.get("server"),
        "JIRA_EMAIL": merged.get("jira_email"),
        "JIRA_PROJECT_KEY": merged.get("jira_project_key"),
        "JIRA_DEFAULT_ISSUE_TYPE": merged.get("default_issue_type"),
        "JIRA_DEFAULT_PRIORITY": merged.get("default_priority"),
        "JIRA_BACKLOG_LOOKBACK_DAYS": merged.get("backlog_lookback_days"),
        "JIRA_CLOSED_LOOKBACK_DAYS": merged.get("closed_lookback_days"),
        "JIRA_USE_SERVICE_ACCOUNT": merged.get("use_service_account"),
        "JIRA_CLOUD_ID": merged.get("cloud_id"),
        "SCRIBE_NOTES_SOURCE": merged.get("notes_source"),
        "SCRIBE_SEARCH_QUERY": merged.get("search_query"),
        "SCRIBE_LOOKBACK_HOURS": merged.get("lookback_hours"),
        "SCRIBE_MIN_CONFIDENCE": merged.get("min_confidence"),
        "SCRIBE_MODE": merged.get("mode"),
        "SCRIBE_DRY_RUN": merged.get("dry_run"),
        "SCRIBE_THEME_COLOR_INFO": merged.get("color_info"),
        "SCRIBE_THEME_COLOR_WARNING": merged.get("color_warning"),
    }
    for key, value in non_secrets.items():
        if value is not None:
            os.environ.setdefault(key, str(value))

    # Secrets — team's env_suffix drives which {KEY}_{SUFFIX} variable to look up
    suffix = team.get("env_suffix", team_name.upper())
    resolver = _build_resolver(suffix)

    # strict=True: every team must provision their own Jira credentials
    os.environ["JIRA_API_TOKEN"] = resolver.get("JIRA_API_TOKEN", strict=True)
    # strict=True: every team must provision their own GCP service account key
    gcp_json = resolver.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", strict=True)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _write_credential_json(gcp_json)
    os.environ.pop(f"GOOGLE_APPLICATION_CREDENTIALS_JSON_{suffix.upper()}", None)
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS_JSON", None)
    # optional
    slack = resolver.get_optional("SCRIBE_SLACK_WEBHOOK_URL")
    if slack:
        os.environ["SCRIBE_SLACK_WEBHOOK_URL"] = slack

    logger.info("team_config_loaded", extra={"team": team_name, "suffix": suffix})
