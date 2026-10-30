from functools import lru_cache
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repository root, so loading works from any working directory.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

APP_ROLE = "incidentlens_app"
TOOLS_RO_ROLE = "tools_ro"


def _config() -> SettingsConfigDict:
    return SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        # Keep raw URLs (which contain passwords) out of validation error messages.
        hide_input_in_errors=True,
    )


def _parse_user(url: SecretStr, field: str) -> str | None:
    try:
        return conninfo_to_dict(url.get_secret_value()).get("user")
    except Exception:
        pass
    # Raised outside the except block so the original error, whose message may
    # quote part of the URL, is not kept as __context__.
    raise ValueError(f"{field} is not a valid PostgreSQL connection URL")


def _require_user(url: SecretStr, expected: str, field: str) -> SecretStr:
    user = _parse_user(url, field)
    if user != expected:
        raise ValueError(f"{field} must connect as the '{expected}' role")
    return url


class Settings(BaseSettings):
    """Settings for the running API. Deliberately has no admin URL."""

    model_config = _config()

    database_url: SecretStr
    tools_ro_database_url: SecretStr

    # LLM (Groq, OpenAI-compatible API). Without a key the API runs but investigations are disabled.
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_seconds: float = Field(30.0, gt=0, le=120)
    llm_max_retries: int = Field(4, ge=0, le=8)  # SDK retries 429/5xx, honouring retry-after
    # Persistent LLM response cache (llm_cache table). Off by default: cached conversations contain
    # incident evidence, and a re-run with unchanged evidence would replay the old answer. The
    # cache key covers the whole conversation, so new evidence never hits a stale entry.
    llm_cache_enabled: bool = False

    # Public deployment posture (the Docker image enables it): secure-only session cookies and the
    # public demo banner. In EVERY mode, user-created incidents are private to their owner and need
    # a logged-in account; demo incidents are public and read-only.
    public_demo_mode: bool = False

    # Accounts and sessions.
    registration_enabled: bool = True
    session_ttl_hours: int = Field(168, ge=1, le=24 * 90)  # absolute lifetime (default 7 days)
    # None = follow PUBLIC_DEMO_MODE (Secure cookies in public deployments, plain HTTP locally).
    session_cookie_secure: bool | None = None
    login_attempts_per_ip_per_15min: int = Field(20, ge=1, le=10_000)
    login_failures_per_email_per_15min: int = Field(5, ge=1, le=1000)
    registrations_per_ip_per_hour: int = Field(5, ge=1, le=10_000)
    registrations_per_hour: int = Field(100, ge=1, le=100_000)
    max_incidents_per_user: int = Field(20, ge=1, le=10_000)
    max_runs_per_user_per_hour: int = Field(10, ge=1, le=1000)

    # Agent budgets: every investigation is bounded by these.
    agent_max_iterations: int = Field(8, ge=2, le=20)
    agent_max_tool_calls: int = Field(10, ge=1, le=30)
    agent_max_tool_result_chars: int = Field(3500, ge=500, le=20000)
    max_concurrent_runs: int = Field(2, ge=1, le=10)
    # Public demo protection (no auth in the MVP): cap investigations per rolling hour.
    max_runs_per_hour: int = Field(20, ge=1, le=1000)
    # ...and user-created incidents: per rolling hour, and in total.
    max_incidents_per_hour: int = Field(30, ge=1, le=1000)
    max_user_incidents: int = Field(200, ge=1, le=100_000)
    # Evidence stored per user incident (log refs are 4 digits, hence the 9,999 ceiling).
    max_incident_log_entries: int = Field(5000, ge=1, le=9999)
    max_incident_metric_points: int = Field(2000, ge=1, le=100_000)
    max_incident_deployments: int = Field(200, ge=1, le=10_000)
    # Default age after which `python -m app.db.prune` removes user incidents (never demos).
    user_incident_retention_days: int = Field(30, ge=1, le=3650)

    @property
    def cookie_secure(self) -> bool:
        return self.public_demo_mode if self.session_cookie_secure is None else self.session_cookie_secure

    @field_validator("database_url")
    @classmethod
    def _check_app_user(cls, value: SecretStr) -> SecretStr:
        return _require_user(value, APP_ROLE, "DATABASE_URL")

    @field_validator("tools_ro_database_url")
    @classmethod
    def _check_tools_ro_user(cls, value: SecretStr) -> SecretStr:
        return _require_user(value, TOOLS_RO_ROLE, "TOOLS_RO_DATABASE_URL")


class WebSettings(BaseSettings):
    """HTTP-layer settings with no required values, so the app can be created without a database."""

    model_config = _config()

    # Comma-separated browser origins allowed to call the API. Only needed when the frontend
    # is served from a different origin; the default deployment serves both from one origin.
    cors_origins: str = ""
    # Built React app to serve at "/" (frontend/dist by default) when it exists.
    frontend_dist: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    # Hard cap on any request body, enforced while it streams in (chunked uploads included).
    max_request_bytes: int = Field(4_000_000, ge=10_000, le=50_000_000)


class AdminSettings(BaseSettings):
    """Superuser access. Only for bootstrap, migrations, and the guarded test-database reset."""

    model_config = _config()

    database_admin_url: SecretStr

    @field_validator("database_admin_url")
    @classmethod
    def _check_admin_url(cls, value: SecretStr) -> SecretStr:
        _parse_user(value, "DATABASE_ADMIN_URL")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_admin_settings() -> AdminSettings:
    return AdminSettings()
