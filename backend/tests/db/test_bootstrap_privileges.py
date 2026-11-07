"""Bootstrap privilege rules against real PostgreSQL, on the throwaway test database only.

A Neon-style admin (CREATEROLE + database owner, not a superuser) is simulated with SET ROLE
inside the admin_conn transaction, which the fixture always rolls back: the probe roles and the
temporary ownership change never persist. Probe role names keep the real application roles
(incidentlens_app, tools_ro) untouched.
"""

import pytest
from pydantic import SecretStr

from app.db import bootstrap
from app.db.bootstrap import ROLE_ATTRIBUTES, TOOLS_RO_SETTINGS, BootstrapError, ManagedRole

pytestmark = pytest.mark.db

TEST_DB = "incidentlens_test"
PROBE_APP, PROBE_RO = "il_probe_app", "il_probe_tools_ro"


def probe_roles():
    return [
        ManagedRole(PROBE_APP, SecretStr("probe-app-password-0001")),
        ManagedRole(PROBE_RO, SecretStr("probe-ro-password-00002"), dict(TOOLS_RO_SETTINGS)),
    ]


def act_as(conn, name, attributes, owns_test_db):
    """Create a NOLOGIN admin role and switch to it (all rolled back by the fixture)."""
    conn.execute(f"CREATE ROLE {name} NOLOGIN {attributes}")
    if owns_test_db:
        conn.execute(f"ALTER DATABASE {TEST_DB} OWNER TO {name}")
    conn.execute(f"SET ROLE {name}")


def probe_role_count(conn):
    return conn.execute("SELECT count(*) FROM pg_roles WHERE rolname IN (%s, %s)", (PROBE_APP, PROBE_RO)).fetchone()[0]


def test_local_superuser_is_accepted(admin_conn):
    privileges = bootstrap.read_admin_privileges(admin_conn)
    assert privileges.superuser is True

    assert bootstrap.apply_roles(admin_conn, probe_roles()) == [(PROBE_APP, "created"), (PROBE_RO, "created")]


def test_neon_style_owner_creates_restricted_roles(admin_conn):
    act_as(admin_conn, "il_probe_owner", "CREATEROLE", owns_test_db=True)
    assert bootstrap.read_admin_privileges(admin_conn) == bootstrap.AdminPrivileges(
        superuser=False, createrole=True, owns_database=True)

    assert bootstrap.apply_roles(admin_conn, probe_roles()) == [(PROBE_APP, "created"), (PROBE_RO, "created")]

    rows = {row[0]: row for row in bootstrap.describe_roles(admin_conn, [PROBE_APP, PROBE_RO])}
    for name in (PROBE_APP, PROBE_RO):
        _, can_login, superuser, createdb, createrole, inherit, replication, bypassrls, _ = rows[name]
        # Exactly the restricted attributes, even though the creator is not a superuser.
        assert (can_login, superuser, createdb, createrole, inherit, replication, bypassrls) == (
            True, False, False, False, False, False, False), name
    assert set(rows[PROBE_RO][-1]) == {"default_transaction_read_only=on", "statement_timeout=5s"}
    assert "NOSUPERUSER" in ROLE_ATTRIBUTES

    # Reruns update the roles it created (PostgreSQL 16+ gives the creator ADMIN OPTION).
    assert bootstrap.apply_roles(admin_conn, probe_roles()) == [(PROBE_APP, "updated"), (PROBE_RO, "updated")]


def test_member_of_the_owning_role_counts_as_owner(admin_conn):
    admin_conn.execute("CREATE ROLE il_probe_db_owner NOLOGIN")
    admin_conn.execute(f"ALTER DATABASE {TEST_DB} OWNER TO il_probe_db_owner")
    act_as(admin_conn, "il_probe_member", "CREATEROLE INHERIT IN ROLE il_probe_db_owner", owns_test_db=False)

    assert bootstrap.read_admin_privileges(admin_conn).owns_database is True


@pytest.mark.parametrize(
    ("attributes", "owns_test_db"),
    [("CREATEROLE", False), ("", True), ("", False)],
    ids=["createrole-without-ownership", "owner-without-createrole", "no-privileges"],
)
def test_insufficient_roles_are_refused_before_any_change(admin_conn, attributes, owns_test_db):
    act_as(admin_conn, "il_probe_admin", attributes, owns_test_db)

    with pytest.raises(BootstrapError, match="CREATEROLE that owns the target database"):
        bootstrap.apply_roles(admin_conn, probe_roles())

    admin_conn.execute("RESET ROLE")
    assert probe_role_count(admin_conn) == 0


def test_ownership_is_checked_on_the_target_database(admin_conn):
    act_as(admin_conn, "il_probe_owner", "CREATEROLE", owns_test_db=False)
    assert bootstrap.read_admin_privileges(admin_conn).owns_database is False  # owns nothing yet

    admin_conn.execute("RESET ROLE")
    admin_conn.execute(f"ALTER DATABASE {TEST_DB} OWNER TO il_probe_owner")
    admin_conn.execute("SET ROLE il_probe_owner")
    assert admin_conn.execute("SELECT current_database()").fetchone()[0] == TEST_DB
    assert bootstrap.read_admin_privileges(admin_conn).owns_database is True


def test_owner_cannot_take_over_roles_created_by_someone_else(admin_conn):
    # A role that already exists, created by the superuser: the non-superuser owner holds no
    # ADMIN OPTION on it, so it must be refused with an explanation, not altered.
    admin_conn.execute(f"CREATE ROLE {PROBE_APP} NOLOGIN")
    act_as(admin_conn, "il_probe_owner", "CREATEROLE", owns_test_db=True)

    with pytest.raises(BootstrapError, match=f"not allowed to update role {PROBE_APP}"):
        bootstrap.apply_roles(admin_conn, probe_roles())


def test_drifted_privilege_is_detected_and_refused(admin_conn):
    # The owner created the roles; later someone gave the app role CREATEDB. A non-superuser
    # cannot remove it, so bootstrap must stop instead of reporting success.
    act_as(admin_conn, "il_probe_owner", "CREATEROLE CREATEDB", owns_test_db=True)
    bootstrap.apply_roles(admin_conn, probe_roles())
    admin_conn.execute("RESET ROLE")
    admin_conn.execute(f"ALTER ROLE {PROBE_APP} CREATEDB")
    admin_conn.execute("SET ROLE il_probe_owner")

    with pytest.raises(BootstrapError, match=f"role {PROBE_APP} does not have the required restricted attributes"):
        bootstrap.apply_roles(admin_conn, probe_roles())
