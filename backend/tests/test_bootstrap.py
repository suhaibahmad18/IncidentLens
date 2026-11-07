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


# --- admin privilege check: superuser, or CREATEROLE + ownership of the target database ---


class FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class FakePgConn:
    ssl_in_use = True

    @staticmethod
    def encrypt_password(password, user, algorithm):
        return FAKE_HASH.encode()


class FakeAdminConnection:
    """Answers the privilege query with `privileges` and records every statement."""

    def __init__(self, privileges, deny_role_statements=False, role_flags=bootstrap.REQUIRED_ROLE_FLAGS,
                 existing=False):
        self.privileges = privileges
        self.existing = existing
        self.deny_role_statements = deny_role_statements
        self.role_flags = role_flags
        self.statements = []
        self.pgconn = FakePgConn()

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else repr(query)
        self.statements.append(text)
        if query == bootstrap.ADMIN_PRIVILEGES_QUERY:
            return FakeResult(self.privileges)
        if "rolcanlogin, rolsuper" in text:
            return FakeResult(self.role_flags)
        if text.startswith("SELECT 1 FROM pg_roles"):
            return FakeResult((1,) if self.existing else None)
        if self.deny_role_statements and "ROLE" in text and "pg_roles" not in text:
            raise psycopg.errors.InsufficientPrivilege(f"permission denied; hash {FAKE_HASH}")
        return FakeResult(None)  # role does not exist yet; advisory lock etc.


def managed_roles():
    return check_preflight(**valid_urls())


@pytest.mark.parametrize(
    ("superuser", "createrole", "owns_database"),
    [(True, False, False), (True, True, True), (False, True, True)],
    ids=["local-superuser", "superuser-with-everything", "neon-style-owner"],
)
def test_sufficient_admin_privileges_are_accepted(superuser, createrole, owns_database):
    bootstrap.check_admin_privileges(bootstrap.AdminPrivileges(superuser, createrole, owns_database))


@pytest.mark.parametrize(
    ("createrole", "owns_database"),
    [(True, False), (False, True), (False, False)],
    ids=["createrole-without-ownership", "owner-without-createrole", "no-privileges"],
)
def test_insufficient_admin_privileges_are_refused(createrole, owns_database):
    with pytest.raises(BootstrapError, match="superuser, or as a role with CREATEROLE that owns the target database"):
        bootstrap.check_admin_privileges(bootstrap.AdminPrivileges(False, createrole, owns_database))


def test_privileges_are_read_for_the_connected_database_and_role():
    query = bootstrap.ADMIN_PRIVILEGES_QUERY
    # Ownership is checked on the database this connection targets, for the role in use.
    assert "d.datname = current_database()" in query
    assert "r.rolname = current_user" in query
    assert "pg_has_role(current_user, d.datdba, 'USAGE')" in query

    conn = FakeAdminConnection((False, True, True))
    assert bootstrap.read_admin_privileges(conn) == bootstrap.AdminPrivileges(False, True, True)


def test_unreadable_privileges_are_refused():
    with pytest.raises(BootstrapError, match="could not read the privileges"):
        bootstrap.read_admin_privileges(FakeAdminConnection(None))


@pytest.mark.parametrize("privileges", [(False, True, False), (False, False, True), (False, False, False)])
def test_insufficient_admin_changes_nothing(privileges):
    conn = FakeAdminConnection(privileges)

    with pytest.raises(BootstrapError):
        bootstrap.apply_roles(conn, managed_roles())

    # Refused before the lock and before any role statement.
    assert conn.statements == [bootstrap.ADMIN_PRIVILEGES_QUERY]


@pytest.mark.parametrize("privileges", [(True, False, False), (False, True, True)], ids=["superuser", "neon-owner"])
def test_sufficient_admin_creates_both_roles(privileges):
    conn = FakeAdminConnection(privileges)

    assert bootstrap.apply_roles(conn, managed_roles()) == [("incidentlens_app", "created"), ("tools_ro", "created")]
    assert any("CREATE" in s and "incidentlens_app" in s for s in conn.statements)


def test_denied_role_statement_gives_a_safe_explanation():
    conn = FakeAdminConnection((False, True, True), deny_role_statements=True)

    with pytest.raises(BootstrapError) as info:
        bootstrap.apply_roles(conn, managed_roles())

    message = str(info.value)
    assert "not allowed to create role incidentlens_app" in message and "ADMIN OPTION" in message
    assert FAKE_HASH not in message and APP_PW not in message
    assert info.value.__context__ is None  # no chained database error that could carry details


def test_main_reports_insufficient_admin_safely(fake_settings, monkeypatch, capsys):
    class Connection(FakeAdminConnection):
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(bootstrap, "connect", lambda url, **kwargs: Connection((False, True, False)))

    assert bootstrap.main() == 1
    assert_output_is_safe(capsys, "while creating or updating roles",
                          "must connect as a superuser, or as a role with CREATEROLE that owns the target database")


def role_statements(conn):
    return [s for s in conn.statements if "ROLE" in s and "PASSWORD" in s]


def test_superuser_update_restates_every_restriction():
    conn = FakeAdminConnection((True, False, False), existing=True)

    assert bootstrap.apply_roles(conn, managed_roles()) == [("incidentlens_app", "updated"), ("tools_ro", "updated")]

    statements = role_statements(conn)
    assert len(statements) == 2 and all(ROLE_ATTRIBUTES in s and "ALTER" in s for s in statements)


def test_non_superuser_update_changes_only_what_it_may():
    conn = FakeAdminConnection((False, True, True), existing=True)

    bootstrap.apply_roles(conn, managed_roles())

    for statement in role_statements(conn):
        assert bootstrap.NON_SUPERUSER_ALTER_ATTRIBUTES in statement
        for forbidden in ("NOSUPERUSER", "NOCREATEDB", "NOREPLICATION", "NOBYPASSRLS"):
            assert forbidden not in statement


def test_non_superuser_create_still_states_every_restriction():
    conn = FakeAdminConnection((False, True, True))

    bootstrap.apply_roles(conn, managed_roles())

    assert all(ROLE_ATTRIBUTES in s and "CREATE" in s for s in role_statements(conn))


@pytest.mark.parametrize("index", range(7), ids=["login", "superuser", "createdb", "createrole", "inherit",
                                                 "replication", "bypassrls"])
def test_role_with_any_unexpected_attribute_is_refused(index):
    flags = list(bootstrap.REQUIRED_ROLE_FLAGS)
    flags[index] = not flags[index]
    conn = FakeAdminConnection((False, True, True), existing=True, role_flags=tuple(flags))

    with pytest.raises(BootstrapError, match="does not have the required restricted attributes"):
        bootstrap.apply_roles(conn, managed_roles())


# --- Neon: plaintext passwords (opt-in, TLS only) ---


def test_passwords_are_hashed_client_side_by_default(monkeypatch):
    monkeypatch.delenv(bootstrap.PLAINTEXT_PASSWORDS_ENV, raising=False)
    conn = FakeAdminConnection((False, True, True))

    bootstrap.apply_roles(conn, managed_roles())

    statements = " ".join(role_statements(conn))
    assert FAKE_HASH in statements and APP_PW not in statements and RO_PW not in statements


def test_plaintext_opt_in_sends_the_password_for_the_server_to_hash(monkeypatch):
    monkeypatch.setenv(bootstrap.PLAINTEXT_PASSWORDS_ENV, "1")
    conn = FakeAdminConnection((False, True, True))

    bootstrap.apply_roles(conn, managed_roles())

    statements = " ".join(role_statements(conn))
    assert APP_PW in statements and RO_PW in statements and FAKE_HASH not in statements


def test_plaintext_opt_in_refuses_an_unencrypted_connection(monkeypatch):
    monkeypatch.setenv(bootstrap.PLAINTEXT_PASSWORDS_ENV, "1")
    conn = FakeAdminConnection((False, True, True))
    conn.pgconn = type("NoTls", (FakePgConn,), {"ssl_in_use": False})()

    with pytest.raises(BootstrapError, match="requires a TLS-encrypted admin connection"):
        bootstrap.apply_roles(conn, managed_roles())
    assert not role_statements(conn)
