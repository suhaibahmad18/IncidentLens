from types import SimpleNamespace

import psycopg
import pytest
from pydantic import SecretStr, ValidationError

from app.config import ENV_FILE, AdminSettings, Settings
from app.db.connection import connect

# Fake values only. These tests never read the real .env file or touch the real database.
APP_PASSWORD = "fake-app-pw-123"
RO_PASSWORD = "fake-ro-pw-456"
ADMIN_PASSWORD = "fake-admin-pw-789"

APP_URL = f"postgresql://incidentlens_app:{APP_PASSWORD}@127.0.0.1:5432/incidentlens"
RO_URL = f"postgresql://tools_ro:{RO_PASSWORD}@127.0.0.1:5432/incidentlens"
ADMIN_URL = f"postgresql://postgres:{ADMIN_PASSWORD}@127.0.0.1:5432/incidentlens"

ALL_VARS = ("DATABASE_URL", "TOOLS_RO_DATABASE_URL", "DATABASE_ADMIN_URL")

# psycopg quotes these malformed URLs, password included, in its own error messages.
MALFORMED_URLS = {
    "bad-percent-encoding": "postgresql://incidentlens_app:{pw}%zz@127.0.0.1:5432/incidentlens",
    "unclosed-ipv6-bracket": "postgresql://incidentlens_app:{pw}@[bad-host/incidentlens",
}


@pytest.fixture
def clean_env(monkeypatch):
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def assert_no_leak(exc: BaseException, secret: str) -> None:
    """The exception exposes the secret through neither its text nor its attributes."""
    assert secret not in str(exc)
    assert secret not in repr(exc)
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert getattr(exc, "pgconn", None) is None


def test_env_file_points_to_repository_root():
    assert ENV_FILE.name == ".env"
    assert (ENV_FILE.parent / "docker-compose.yml").is_file()


def test_settings_load_from_environment(clean_env):
    clean_env.setenv("DATABASE_URL", APP_URL)
    clean_env.setenv("TOOLS_RO_DATABASE_URL", RO_URL)

    settings = Settings(_env_file=None)

    assert settings.database_url.get_secret_value() == APP_URL
    assert settings.tools_ro_database_url.get_secret_value() == RO_URL


def test_settings_load_from_env_file(clean_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DATABASE_URL={APP_URL}\nTOOLS_RO_DATABASE_URL={RO_URL}\nDATABASE_ADMIN_URL={ADMIN_URL}\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)
    admin = AdminSettings(_env_file=env_file)

    assert settings.database_url.get_secret_value() == APP_URL
    assert admin.database_admin_url.get_secret_value() == ADMIN_URL


def test_missing_settings_are_rejected(clean_env):
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secrets_are_masked_in_output():
    settings = Settings(_env_file=None, database_url=APP_URL, tools_ro_database_url=RO_URL)
    admin = AdminSettings(_env_file=None, database_admin_url=ADMIN_URL)

    rendered = " ".join(
        [repr(settings), str(settings), settings.model_dump_json(), repr(admin), str(admin)]
    )

    for secret in (APP_PASSWORD, RO_PASSWORD, ADMIN_PASSWORD):
        assert secret not in rendered
    assert "**********" in rendered


@pytest.mark.parametrize(
    ("field", "bad_url", "password"),
    [
        ("database_url", ADMIN_URL, ADMIN_PASSWORD),
        ("database_url", RO_URL, RO_PASSWORD),
        ("tools_ro_database_url", APP_URL, APP_PASSWORD),
        ("tools_ro_database_url", ADMIN_URL, ADMIN_PASSWORD),
    ],
    ids=["app-as-admin", "app-as-tools_ro", "tools_ro-as-app", "tools_ro-as-admin"],
)
def test_wrong_database_user_is_rejected_without_leaking(field, bad_url, password):
    values = {"database_url": APP_URL, "tools_ro_database_url": RO_URL, field: bad_url}

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **values)

    message = str(exc_info.value)
    assert "must connect as" in message
    assert password not in message
    assert bad_url not in message


@pytest.mark.parametrize("template", MALFORMED_URLS.values(), ids=MALFORMED_URLS.keys())
def test_malformed_app_url_is_rejected_without_leaking(template):
    bad_url = template.format(pw=APP_PASSWORD)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, database_url=bad_url, tools_ro_database_url=RO_URL)

    assert APP_PASSWORD not in str(exc_info.value)
    # The ValueError Pydantic wraps must not keep psycopg's leaking error as context.
    assert_no_leak(exc_info.value.errors()[0]["ctx"]["error"], APP_PASSWORD)


@pytest.mark.parametrize("template", MALFORMED_URLS.values(), ids=MALFORMED_URLS.keys())
def test_malformed_admin_url_is_rejected_without_leaking(template):
    bad_url = template.format(pw=ADMIN_PASSWORD)

    with pytest.raises(ValidationError) as exc_info:
        AdminSettings(_env_file=None, database_admin_url=bad_url)

    assert "DATABASE_ADMIN_URL is not a valid PostgreSQL connection URL" in str(exc_info.value)
    assert ADMIN_PASSWORD not in str(exc_info.value)
    assert_no_leak(exc_info.value.errors()[0]["ctx"]["error"], ADMIN_PASSWORD)


@pytest.mark.parametrize("template", MALFORMED_URLS.values(), ids=MALFORMED_URLS.keys())
def test_connect_with_malformed_url_does_not_leak(template):
    # Parsing fails before any network activity.
    bad_url = SecretStr(template.format(pw=ADMIN_PASSWORD))

    with pytest.raises(ValueError, match="Invalid PostgreSQL connection URL") as exc_info:
        connect(bad_url)

    assert_no_leak(exc_info.value, ADMIN_PASSWORD)


def test_failed_connection_does_not_leak(monkeypatch):
    # When a server rejects a connection (e.g. bad password), psycopg attaches a snapshot
    # of the connection, password included, as `pgconn`. Simulated here: no network.
    message = 'connection to server at "127.0.0.1", port 5432 failed: FATAL: password authentication failed'
    rejected = psycopg.OperationalError(
        message, pgconn=SimpleNamespace(password=ADMIN_PASSWORD.encode())
    )
    assert rejected.pgconn.password == ADMIN_PASSWORD.encode()  # the risk is real

    def fake_connect(*args, **kwargs):
        raise rejected

    monkeypatch.setattr(psycopg, "connect", fake_connect)

    with pytest.raises(psycopg.OperationalError) as exc_info:
        connect(SecretStr(ADMIN_URL))

    assert str(exc_info.value) == message  # the useful message survives
    assert_no_leak(exc_info.value, ADMIN_PASSWORD)


def test_app_settings_never_expose_admin_url(clean_env):
    clean_env.setenv("DATABASE_URL", APP_URL)
    clean_env.setenv("TOOLS_RO_DATABASE_URL", RO_URL)
    clean_env.setenv("DATABASE_ADMIN_URL", ADMIN_URL)

    settings = Settings(_env_file=None)

    assert "database_admin_url" not in Settings.model_fields
    assert not hasattr(settings, "database_admin_url")
    assert ADMIN_PASSWORD not in settings.model_dump_json()
    assert not any("admin" in key for key in settings.model_dump())
    secret_fields = {k for k, v in Settings.model_fields.items() if "SecretStr" in str(v.annotation)}
    assert secret_fields == {"database_url", "tools_ro_database_url", "groq_api_key"}


def test_admin_settings_hold_only_admin_url(clean_env):
    clean_env.setenv("DATABASE_URL", APP_URL)
    clean_env.setenv("TOOLS_RO_DATABASE_URL", RO_URL)
    clean_env.setenv("DATABASE_ADMIN_URL", ADMIN_URL)

    admin = AdminSettings(_env_file=None)

    assert set(admin.model_dump()) == {"database_admin_url"}
