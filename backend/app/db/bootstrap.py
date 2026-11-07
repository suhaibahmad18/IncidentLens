"""Create or update the IncidentLens login roles.

Run from backend/:  uv run python -m app.db.bootstrap

Scope: role attributes only. This module never grants or revokes data access; database,
schema, and table permissions are applied by the migrations. Until then the roles keep
PostgreSQL's default PUBLIC rights (CONNECT, TEMPORARY, USAGE on schema public).

The admin connection must be a superuser (local Docker PostgreSQL) or, on managed services
without superuser access such as Neon, a role with CREATEROLE that owns the target database.
That is everything the release needs: creating these restricted roles requires CREATEROLE, and
the database/schema grants in the migrations require database ownership.

Output never contains passwords, password hashes, connection URLs, or exception messages.
"""

import os
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
# PostgreSQL 16+ refuses to let a non-superuser even restate SUPERUSER, CREATEDB, REPLICATION or
# BYPASSRLS on an existing role (NOSUPERUSER always needs a superuser). Such an admin updates only
# what it may change; verify_role_attributes then proves the full restricted set still holds.
NON_SUPERUSER_ALTER_ATTRIBUTES = "LOGIN NOCREATEROLE NOINHERIT"
# (can login, superuser, createdb, createrole, inherit, replication, bypassrls) after every change.
REQUIRED_ROLE_FLAGS = (True, False, False, False, False, False, False)
TOOLS_RO_SETTINGS = {"default_transaction_read_only": "on", "statement_timeout": "5s"}
# Neon's control plane rejects pre-hashed passwords ("Neon only supports being given plaintext
# passwords"). Opting in sends the password itself (the server hashes it), and only over TLS.
PLAINTEXT_PASSWORDS_ENV = "BOOTSTRAP_SEND_PLAINTEXT_PASSWORDS"
# Arbitrary constant so concurrent bootstrap runs serialize instead of racing.
ADVISORY_LOCK_KEY = 7_204_311

REPORT_COLUMNS = (
    "rolname", "rolcanlogin", "rolsuper", "rolcreatedb", "rolcreaterole",
    "rolinherit", "rolreplication", "rolbypassrls", "rolconfig",
)


class BootstrapError(Exception):
    """A failure whose message is safe to print: it names settings, never their values."""


# What the admin connection may do, read for the database it is connected to (the target:
# check_preflight guarantees all three URLs name the same database). "Owns" uses PostgreSQL's
# own rule: the owner itself, or a member that inherits the owning role's privileges.
ADMIN_PRIVILEGES_QUERY = (
    "SELECT r.rolsuper, r.rolcreaterole, pg_has_role(current_user, d.datdba, 'USAGE')"
    " FROM pg_roles r CROSS JOIN pg_database d"
    " WHERE r.rolname = current_user AND d.datname = current_database()"
)
INSUFFICIENT_ADMIN = (
    "DATABASE_ADMIN_URL must connect as a superuser, or as a role with CREATEROLE"
    " that owns the target database"
)


@dataclass(frozen=True)
class AdminPrivileges:
    superuser: bool
    createrole: bool
    owns_database: bool


def read_admin_privileges(conn: psycopg.Connection) -> AdminPrivileges:
    row = conn.execute(ADMIN_PRIVILEGES_QUERY).fetchone()
    if row is None:
        raise BootstrapError("could not read the privileges of the DATABASE_ADMIN_URL role")
    return AdminPrivileges(*(bool(value) for value in row))


def check_admin_privileges(privileges: AdminPrivileges) -> None:
    """Superuser, or CREATEROLE plus ownership of the target database. Anything else is refused."""
    if privileges.superuser or (privileges.createrole and privileges.owns_database):
        return
    raise BootstrapError(INSUFFICIENT_ADMIN)


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


def role_statement(name: str, password_hash: str, exists: bool, attributes: str = ROLE_ATTRIBUTES) -> sql.Composed:
    """CREATE or ALTER a role. Takes a SCRAM hash, never a plaintext password."""
    return sql.SQL("{verb} ROLE {name} WITH {attributes} PASSWORD {password}").format(
        verb=sql.SQL("ALTER" if exists else "CREATE"),
        name=sql.Identifier(name),
        attributes=sql.SQL(attributes),
        password=sql.Literal(password_hash),
    )


def verify_role_attributes(conn: psycopg.Connection, name: str) -> None:
    """Read the role back and refuse to continue unless every restriction holds."""
    row = conn.execute(
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls"
        " FROM pg_roles WHERE rolname = %s",
        (name,),
    ).fetchone()
    if row is None or tuple(bool(value) for value in row) != REQUIRED_ROLE_FLAGS:
        raise BootstrapError(
            f"role {name} does not have the required restricted attributes and the"
            " DATABASE_ADMIN_URL role cannot change that; fix the role as a superuser and rerun"
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
    privileges = read_admin_privileges(conn)
    check_admin_privileges(privileges)
    send_plaintext = os.environ.get(PLAINTEXT_PASSWORDS_ENV) == "1"
    if send_plaintext and not conn.pgconn.ssl_in_use:
        raise BootstrapError(f"{PLAINTEXT_PASSWORDS_ENV}=1 requires a TLS-encrypted admin connection")

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

        if send_plaintext:  # Neon: the server hashes it; the connection is TLS (checked above)
            password_hash = role.password.get_secret_value()
        else:  # hashed client-side, so the plaintext password is never sent to the server
            password_hash = conn.pgconn.encrypt_password(
                role.password.get_secret_value().encode(), role.name.encode(), b"scram-sha-256"
            ).decode()
        denied = False
        try:
            attributes = ROLE_ATTRIBUTES if privileges.superuser or not exists else NON_SUPERUSER_ALTER_ATTRIBUTES
            conn.execute(role_statement(role.name, password_hash, exists, attributes))
            for statement in setting_statements(role.name, role.settings):
                conn.execute(statement)
        except psycopg.errors.InsufficientPrivilege:
            denied = True
        if denied:  # raised outside the except block: no chained database error
            # PostgreSQL 16+: a non-superuser may only change roles it holds ADMIN OPTION on,
            # which it gets automatically for the roles it created itself.
            raise BootstrapError(
                f"the DATABASE_ADMIN_URL role is not allowed to {'update' if exists else 'create'}"
                f" role {role.name} (a non-superuser needs ADMIN OPTION on an existing role)"
            )
        verify_role_attributes(conn, role.name)
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
