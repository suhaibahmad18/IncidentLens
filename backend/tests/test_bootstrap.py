"""Offline tests for app.db.bootstrap. No database connections; fake values only."""

import psycopg
import pytest
from pydantic import SecretStr

from app.db import bootstrap
from app.db.bootstrap import (
    ROLE_ATTRIBUTES,
    BootstrapError,
    check_preflight,
    role_statement,
    setting_statements,
)

ADMIN_PW = "fake-admin-password-0001"
APP_PW = "fake-app-password-00002"
RO_PW = "fake-ro-password-000003"
FAKE_HASH = "SCRAM-SHA-256$4096:ZmFrZXNhbHQ=$ZmFrZXN0b3JlZGtleQ==:ZmFrZXNlcnZlcmtleQ=="


def url(user, password, host="127.0.0.1", port="5432", dbname="incidentlens"):
    return SecretStr(f"postgresql://{user}:{password}@{host}:{port}/{dbname}")


def valid_urls(**overrides):
    urls = {
        "admin_url": url("postgres", ADMIN_PW),
        "app_url": url("incidentlens_app", APP_PW),
        "tools_ro_url": url("tools_ro", RO_PW),
    }
    urls.update(overrides)
    return urls


def assert_safe_error(exc: BaseException, *secrets: str) -> None:
    for secret in (ADMIN_PW, APP_PW, RO_PW, *secrets):
        assert secret not in str(exc)
        assert secret not in repr(exc)
    assert exc.__cause__ is None
    assert exc.__context__ is None


def test_preflight_accepts_valid_configuration():
    roles = check_preflight(**valid_urls())

    assert [role.name for role in roles] == ["incidentlens_app", "tools_ro"]
    assert roles[0].password.get_secret_value() == APP_PW
    assert roles[1].password.get_secret_value() == RO_PW
    assert roles[0].settings == {}
    assert roles[1].settings == {
        "default_transaction_read_only": "on",
        "statement_timeout": "5s",
    }


def test_managed_role_masks_password():
    roles = check_preflight(**valid_urls())

    rendered = repr(roles) + str(roles)
    assert APP_PW not in rendered
    assert RO_PW not in rendered


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"app_url": url("incidentlens_app", "change-me-app")}, "DATABASE_URL still contains a placeholder"),
        ({"tools_ro_url": url("tools_ro", "change-me-ro-xxxxxxxx")}, "TOOLS_RO_DATABASE_URL still contains a placeholder"),
        ({"admin_url": url("postgres", "change-me-admin-xxxxxx")}, "DATABASE_ADMIN_URL still contains a placeholder"),
        ({"app_url": url("incidentlens_app", "short-pw-123")}, "DATABASE_URL password is shorter than 16"),
        ({"app_url": SecretStr("postgresql://incidentlens_app@127.0.0.1:5432/incidentlens")}, "DATABASE_URL has no password"),
        ({"tools_ro_url": url("tools_ro", APP_PW)}, "must be different"),
        ({"app_url": url("incidentlens_app", ADMIN_PW)}, "must be different"),
        ({"app_url": url("incidentlens_app", APP_PW, host="db.example.com")}, "same host, port, and database"),
        ({"tools_ro_url": url("tools_ro", RO_PW, port="5433")}, "same host, port, and database"),
        ({"tools_ro_url": url("tools_ro", RO_PW, dbname="incidentlens_test")}, "same host, port, and database"),
        ({"admin_url": url("incidentlens_app", ADMIN_PW)}, "must not use a managed application role"),
        ({"app_url": url("postgres", APP_PW)}, "DATABASE_URL must connect as the 'incidentlens_app' role"),
        ({"tools_ro_url": url("incidentlens_app", RO_PW)}, "TOOLS_RO_DATABASE_URL must connect as the 'tools_ro' role"),
    ],
    ids=[
        "app-placeholder", "tools_ro-placeholder", "admin-placeholder", "too-short",
        "missing-password", "reused-app-ro", "reused-app-admin", "different-host",
        "different-port", "different-dbname", "admin-is-managed-role", "wrong-app-user",
        "wrong-tools_ro-user",
    ],
)
def test_preflight_rejects_unsafe_configuration(overrides, expected):
    with pytest.raises(BootstrapError, match=expected) as exc_info:
        check_preflight(**valid_urls(**overrides))

    assert_safe_error(exc_info.value, "change-me", "short-pw-123")


def test_preflight_rejects_malformed_url_without_leaking():
    bad = SecretStr(f"postgresql://incidentlens_app:{APP_PW}%zz@127.0.0.1:5432/incidentlens")

    with pytest.raises(BootstrapError, match="DATABASE_URL is not a valid") as exc_info:
        check_preflight(**valid_urls(app_url=bad))

    assert_safe_error(exc_info.value)


@pytest.mark.parametrize(("exists", "verb"), [(False, "CREATE"), (True, "ALTER")])
def test_role_statement_uses_hash_and_restricted_attributes(exists, verb):
    text = role_statement("tools_ro", FAKE_HASH, exists).as_string(None)

    assert text.startswith(f'{verb} ROLE "tools_ro" WITH ')
    for attribute in ROLE_ATTRIBUTES.split():
        assert attribute in text.split()
    assert "NOSUPERUSER" in text and "SUPERUSER " not in text.replace("NOSUPERUSER", "")
    assert f"PASSWORD '{FAKE_HASH}'" in text


def test_role_statement_quotes_identifier():
    text = role_statement('evil"; DROP ROLE postgres; --', FAKE_HASH, False).as_string(None)

    assert text.startswith('CREATE ROLE "evil""; DROP ROLE postgres; --" WITH ')


def test_tools_ro_setting_statements():
    texts = [
        statement.as_string(None)
        for statement in setting_statements("tools_ro", bootstrap.TOOLS_RO_SETTINGS)
    ]

    assert texts == [
        'ALTER ROLE "tools_ro" SET "default_transaction_read_only" = \'on\'',
        'ALTER ROLE "tools_ro" SET "statement_timeout" = \'5s\'',
    ]


def test_module_grants_nothing():
    """Stage 4 scope: role attributes only. Data-access grants belong to migrations."""
    source = open(bootstrap.__file__, encoding="utf-8").read()
    code_lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    code = "\n".join(code_lines).split('"""', 2)[2]  # skip the module docstring

    for keyword in ("GRANT ", "REVOKE ", "DROP ROLE", "CREATE TABLE", "CREATE DATABASE"):
        assert keyword not in code.upper()


# --- main(): failure output must never include secrets or exception messages ---


class FakeSettings:
    database_url = url("incidentlens_app", APP_PW)
    tools_ro_database_url = url("tools_ro", RO_PW)


class FakeAdminSettings:
    database_admin_url = url("postgres", ADMIN_PW)


@pytest.fixture
def fake_settings(monkeypatch):
    monkeypatch.setattr(bootstrap, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(bootstrap, "get_admin_settings", lambda: FakeAdminSettings())


def assert_output_is_safe(capsys, *must_contain):
    captured = capsys.readouterr()
    output = captured.out + captured.err
    for secret in (ADMIN_PW, APP_PW, RO_PW, "postgresql://", "SCRAM-SHA-256"):
        assert secret not in output
    for text in must_contain:
        assert text in output
    return output


def test_main_settings_failure_prints_type_only(monkeypatch, capsys):
    def broken():
        raise RuntimeError(f"secret detail {ADMIN_PW}")

    monkeypatch.setattr(bootstrap, "get_settings", broken)

    assert bootstrap.main() == 1
    assert_output_is_safe(capsys, "while loading settings: RuntimeError")


def test_main_preflight_failure_prints_safe_message(monkeypatch, capsys):
    class PlaceholderSettings(FakeSettings):
        database_url = url("incidentlens_app", "change-me-app")

    monkeypatch.setattr(bootstrap, "get_settings", lambda: PlaceholderSettings())
    monkeypatch.setattr(bootstrap, "get_admin_settings", lambda: FakeAdminSettings())

    assert bootstrap.main() == 1
    output = assert_output_is_safe(capsys, "while checking configuration", "placeholder")
    assert "change-me-app" not in output


def test_main_connection_failure_prints_type_only(fake_settings, monkeypatch, capsys):
    def refuse(url, **kwargs):
        raise psycopg.OperationalError(f"failed for postgresql://postgres:{ADMIN_PW}@127.0.0.1")

    monkeypatch.setattr(bootstrap, "connect", refuse)

    assert bootstrap.main() == 1
    assert_output_is_safe(capsys, "while connecting as admin: OperationalError")


def test_main_role_failure_prints_type_only(fake_settings, monkeypatch, capsys):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def fail_apply(conn, roles):
        raise psycopg.errors.InsufficientPrivilege(f"denied; hash {FAKE_HASH}")

    monkeypatch.setattr(bootstrap, "connect", lambda url, **kwargs: FakeConnection())
    monkeypatch.setattr(bootstrap, "apply_roles", fail_apply)

    assert bootstrap.main() == 1
    assert_output_is_safe(capsys, "while creating or updating roles: InsufficientPrivilege")
