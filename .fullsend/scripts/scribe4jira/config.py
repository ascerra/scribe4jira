"""Environment configuration for standalone Scribe."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _require(name: str) -> str:
    """Get required environment variable or raise RuntimeError.

        name: Environment variable name

    returns: Environment variable value

    raises: RuntimeError if environment variable is not set or empty
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _optional(name: str, default: str = "") -> str:
    """Get optional environment variable with default value.

        name: Environment variable name
        default: Default value if variable is not set

    returns: Environment variable value or default
    """
    return os.environ.get(name, default).strip()


def _first_non_empty(*names: str) -> str:
    """Return the first non-empty env var value.

        *names: Variable number of environment variable names to check

    returns: Value of first non-empty environment variable, or empty string if all empty
    """
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _resolve_path(path: str) -> str:
    """Resolve relative paths against cwd or module directory.

        path: Relative or absolute path to resolve

    returns: Absolute path, checking if file exists in cwd first, then module dir
    """
    if not path:
        return path
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    for base in (Path.cwd(), Path(__file__).resolve().parent):
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return str(resolved)
    return str((Path.cwd() / candidate).resolve())


def _default_workspace_dir() -> Path:
    """Return the default workspace directory under the system temp dir."""
    return Path(tempfile.gettempdir()) / "scribe-workspace"


def _load_dotenv_if_present() -> None:
    """Load .env file from cwd or module directory if it exists."""
    for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break


def _optional_bool(name: str, default: bool = False) -> bool:
    """Get optional boolean environment variable.

        name: Environment variable name
        default: Default value if variable is not set

    returns: Boolean value parsed from environment variable
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _jira_token() -> str:
    token_file = _optional("JIRA_API_TOKEN_FILE")
    if token_file:
        path = Path(token_file)
        if path.stat().st_size > 16384:
            raise ValueError("Jira token file is unexpectedly large")
        token = path.read_text().strip()
        if not token:
            raise ValueError("Jira token file is empty")
        return token
    return _require("JIRA_API_TOKEN")


def _optional_float(name: str, default: float) -> float:
    """Get optional float environment variable.

        name: Environment variable name
        default: Default value if variable is not set or empty

    returns: Float value parsed from environment variable
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _optional_int(name: str, default: int) -> int:
    """Get optional integer environment variable.

        name: Environment variable name
        default: Default value if variable is not set or empty

    returns: Integer value parsed from environment variable
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True)
class ScribeConfig:
    """Configuration for Scribe pipeline loaded from environment variables."""

    workspace_dir: Path
    notes_dir: Path
    backlog_file: Path
    meta_file: Path
    result_file: Path

    jira_server: str
    jira_email: str
    jira_token: str
    jira_use_service_account: bool
    jira_cloud_id: str
    jira_project_key: str
    jira_default_issue_type: str
    jira_default_priority: str
    jira_backlog_lookback_days: int
    jira_closed_lookback_days: int

    search_query: str
    name_filter: str
    lookback_hours: int

    dry_run: bool
    min_confidence: float
    mode: str

    notes_source: str
    notes_file: str
    drive_credentials: str

    slack_webhook_url: str

    theme_color_info: str
    theme_color_warning: str

    @classmethod
    def from_env(cls, *, load_dotenv: bool = True) -> ScribeConfig:
        """Load configuration from environment variables.

        load_dotenv: when True, load `.env` files before reading variables

        returns: ScribeConfig instance with values loaded from environment

        raises: RuntimeError if required environment variables are missing
        """
        if load_dotenv:
            _load_dotenv_if_present()
        workspace = Path(_optional("SCRIBE_WORKSPACE_DIR", str(_default_workspace_dir())))
        notes_dir = workspace / "notes"
        drive_credentials = _resolve_path(
            _first_non_empty("SCRIBE_DRIVE_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS")
        )
        return cls(
            workspace_dir=workspace,
            notes_dir=notes_dir,
            backlog_file=workspace / "backlog.json",
            meta_file=workspace / "scribe-meta.json",
            result_file=workspace / "agent-result.json",
            jira_server=_require("JIRA_SERVER").rstrip("/"),
            jira_email=_require("JIRA_EMAIL"),
            jira_token=_jira_token(),
            jira_use_service_account=_optional_bool("JIRA_USE_SERVICE_ACCOUNT", False),
            jira_cloud_id=_optional("JIRA_CLOUD_ID", ""),
            jira_project_key=_require("JIRA_PROJECT_KEY"),
            jira_default_issue_type=_optional("JIRA_DEFAULT_ISSUE_TYPE", "Task"),
            jira_default_priority=_optional("JIRA_DEFAULT_PRIORITY", "Undefined"),
            jira_backlog_lookback_days=_optional_int("JIRA_BACKLOG_LOOKBACK_DAYS", 180),
            jira_closed_lookback_days=_optional_int("JIRA_CLOSED_LOOKBACK_DAYS", 90),
            search_query=_optional("SCRIBE_SEARCH_QUERY", ""),
            name_filter=_optional("SCRIBE_NAME_FILTER", ""),
            lookback_hours=_optional_int("SCRIBE_LOOKBACK_HOURS", 3),
            dry_run=_optional_bool("SCRIBE_DRY_RUN", True),
            min_confidence=_optional_float("SCRIBE_MIN_CONFIDENCE", 0.6),
            mode=_optional("SCRIBE_MODE", "all"),
            notes_source=_optional("SCRIBE_NOTES_SOURCE", "drive"),
            notes_file=_resolve_path(_optional("SCRIBE_NOTES_FILE", "")),
            drive_credentials=drive_credentials,
            slack_webhook_url=_optional("SCRIBE_SLACK_WEBHOOK_URL", ""),
            theme_color_info=_optional("SCRIBE_THEME_COLOR_INFO", "#0052CC"),
            theme_color_warning=_optional("SCRIBE_THEME_COLOR_WARNING", "#FF991F"),
        )

    def ensure_workspace(self) -> None:
        """Create workspace and notes directories if they don't exist."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.notes_dir.mkdir(parents=True, exist_ok=True)
