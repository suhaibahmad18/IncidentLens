"""Create or update the IncidentLens login roles.

Run from backend/:  uv run python -m app.db.bootstrap

Scope: role attributes only. This module never grants or revokes data access; database,
schema, and table permissions are applied by the migrations. Until then the roles keep
PostgreSQL's default PUBLIC rights (CONNECT, TEMPORARY, USAGE on schema public).

Output never contains passwords, password hashes, connection URLs, or exception messages.
"""

import sys
from dataclasses import dataclass, field

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from app.config import APP_ROLE, TOOLS_RO_ROLE, get_admin_settings, get_settings
from app.db.connection import connect

MIN_PASSWORD_LENGTH = 16
PLACEHOLDER_MARKER = "change-me"
ROLE_ATTRIBUTES = (
    "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
)
TOOLS_RO_SETTINGS = {"default_transaction_read_only": "on", "statement_timeout": "5s"}
# Arbitrary constant so concurrent bootstrap runs serialize instead of racing.
ADVISORY_LOCK_KEY = 7_204_311

REPORT_COLUMNS = (
    "rolname", "rolcanlogin", "rolsuper", "rolcreatedb", "rolcreaterole",
    "rolinherit", "rolreplication", "rolbypassrls", "rolconfig",
)


class BootstrapError(Exception):
    """A failure whose message is safe to print: it names settings, never their values."""


@dataclass(frozen=True)
class ManagedRole:
    name: str
    password: SecretStr
    settings: dict[str, str] = field(default_factory=dict)


def _parse(url: SecretStr, setting: str) -> dict:
    try:
        return conninfo_to_dict(url.get_secret_value())
    except Exception:
        pass
    # Raised outside the except block so no __context__ links to the original error.
    raise BootstrapError(f"{setting} is not a valid PostgreSQL connection URL")


def _check_password(params: dict, setting: str) -> str:
    password = params.get("password") or ""
    if not password:
        raise BootstrapError(f"{setting} has no password")
    if PLACEHOLDER_MARKER in password:
        raise BootstrapError(f"{setting} still contains a placeholder password")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise BootstrapError(
            f"{setting} password is shorter than {MIN_PASSWORD_LENGTH} characters"
        )
    return password


def _server(params: dict) -> tuple:
    return (params.get("host"), params.get("port") or "5432", params.get("dbname"))


def check_preflight(
    admin_url: SecretStr, app_url: SecretStr, tools_ro_url: SecretStr
) -> list[ManagedRole]:
    """Validate the configuration without connecting. Returns the roles to manage."""
    admin = _parse(admin_url, "DATABASE_ADMIN_URL")
    app = _parse(app_url, "DATABASE_URL")
    tools_ro = _parse(tools_ro_url, "TOOLS_RO_DATABASE_URL")

    if app.get("user") != APP_ROLE:
        raise BootstrapError(f"DATABASE_URL must connect as the '{APP_ROLE}' role")
    if tools_ro.get("user") != TOOLS_RO_ROLE:
        raise BootstrapError(f"TOOLS_RO_DATABASE_URL must connect as the '{TOOLS_RO_ROLE}' role")
    if admin.get("user") in (APP_ROLE, TOOLS_RO_ROLE):
        raise BootstrapError("DATABASE_ADMIN_URL must not use a managed application role")

    passwords = [
        _check_password(admin, "DATABASE_ADMIN_URL"),
        _check_password(app, "DATABASE_URL"),
        _check_password(tools_ro, "TOOLS_RO_DATABASE_URL"),
    ]
    if len(set(passwords)) != len(passwords):
        raise BootstrapError("each of the three database passwords must be different")

    if not _server(admin) == _server(app) == _server(tools_ro):
        raise BootstrapError("all three URLs must use the same host, port, and database")

    return [
        ManagedRole(APP_ROLE, SecretStr(passwords[1])),
        ManagedRole(TOOLS_RO_ROLE, SecretStr(passwords[2]), dict(TOOLS_RO_SETTINGS)),
    ]


def role_statement(name: str, password_hash: str, exists: bool) -> sql.Composed:
    """CREATE or ALTER a role. Takes a SCRAM hash, never a plaintext password."""
    return sql.SQL("{verb} ROLE {name} WITH {attributes} PASSWORD {password}").format(
        verb=sql.SQL("ALTER" if exists else "CREATE"),
        name=sql.Identifier(name),
        attributes=sql.SQL(ROLE_ATTRIBUTES),
        password=sql.Literal(password_hash),
    )


def setting_statements(name: str, settings: dict[str, str]) -> list[sql.Composed]:
    return [
        sql.SQL("ALTER ROLE {name} SET {setting} = {value}").format(
            name=sql.Identifier(name),
            setting=sql.Identifier(setting),
            value=sql.Literal(value),
        )
        for setting, value in settings.items()
    ]


def apply_roles(conn: psycopg.Connection, roles: list[ManagedRole]) -> list[tuple[str, str]]:
    """Create or update every role in the connection's current transaction."""
    is_superuser = conn.execute(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    ).fetchone()[0]
    if not is_superuser:
        raise BootstrapError("DATABASE_ADMIN_URL must connect as a superuser")

    conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))

    results = []
    for role in roles:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role.name,)
        ).fetchone() is not None
        if exists:
            memberships = conn.execute(
                "SELECT g.rolname FROM pg_auth_members m"
                " JOIN pg_roles r ON r.oid = m.member"
                " JOIN pg_roles g ON g.oid = m.roleid"
                " WHERE r.rolname = %s ORDER BY 1",
                (role.name,),
            ).fetchall()
            if memberships:
                names = ", ".join(row[0] for row in memberships)
                raise BootstrapError(
                    f"role {role.name} is a member of other roles ({names});"
                    " remove the membership manually and rerun"
                )

        # Hashed client-side, so the plaintext password is never sent to the server.
        password_hash = conn.pgconn.encrypt_password(
            role.password.get_secret_value().encode(), role.name.encode(), b"scram-sha-256"
        ).decode()
        conn.execute(role_statement(role.name, password_hash, exists))
        for statement in setting_statements(role.name, role.settings):
            conn.execute(statement)
        results.append((role.name, "updated" if exists else "created"))
    return results


def describe_roles(conn: psycopg.Connection, names: list[str]) -> list[tuple]:
    return conn.execute(
        sql.SQL("SELECT {columns} FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname").format(
            columns=sql.SQL(", ").join(map(sql.Identifier, REPORT_COLUMNS))
        ),
        (names,),
    ).fetchall()


def main() -> int:
    step = "loading settings"
    try:
        settings = get_settings()
        admin = get_admin_settings()

        step = "checking configuration"
        roles = check_preflight(
            admin.database_admin_url, settings.database_url, settings.tools_ro_database_url
        )

        step = "connecting as admin"
        with connect(admin.database_admin_url) as conn:
            step = "creating or updating roles"
            results = apply_roles(conn, roles)
            step = "reading role attributes"
            rows = describe_roles(conn, [role.name for role in roles])
            step = "committing"  # the with-block commits on exit, or rolls back on error
    except BootstrapError as exc:
        print(f"bootstrap failed while {step}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Exception messages are not printed: they may contain connection details.
        print(f"bootstrap failed while {step}: {type(exc).__name__}", file=sys.stderr)
        return 1

    for name, action in results:
        print(f"{name}: {action}")
    print()
    print(" | ".join(REPORT_COLUMNS))
    for row in rows:
        print(" | ".join(str(value) for value in row))
    print()
    print("Roles have restricted attributes. Data-access permissions are not configured yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
