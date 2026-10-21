"""Guards that must all pass before pytest may drop or create the test database."""

from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ENV_FILE

ALLOWED_TEST_DATABASE = "incidentlens_test"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
OWNER_MARKER = "incidentlens-pytest-owned"


class UnsafeTestDatabase(Exception):
    """Configuration that could let tests touch a database they don't own."""


class TestDatabaseSettings(BaseSettings):
    __test__ = False  # not a pytest test class

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    test_database_name: str | None = None


def check_test_database_config(test_name: str | None, urls: dict[str, SecretStr]) -> None:
    """Raise UnsafeTestDatabase unless test_name is exactly allowed and distinct from every URL."""
    if test_name != ALLOWED_TEST_DATABASE:
        raise UnsafeTestDatabase(
            f"TEST_DATABASE_NAME must be exactly '{ALLOWED_TEST_DATABASE}'"
        )
    for setting, url in urls.items():
        params = conninfo_to_dict(url.get_secret_value())
        if params.get("dbname") == test_name:
            raise UnsafeTestDatabase(f"{setting} points at the test database; use the dev database")
        if params.get("host") not in LOCAL_HOSTS:
            raise UnsafeTestDatabase(f"{setting} is not a local host; refusing to reset databases")


def check_owner_marker(exists: bool, comment: str | None) -> None:
    """An existing test database may only be dropped if pytest created it."""
    if exists and comment != OWNER_MARKER:
        raise UnsafeTestDatabase(
            f"database '{ALLOWED_TEST_DATABASE}' exists but was not created by pytest; "
            "inspect it and drop it manually if it is safe to do so"
        )
