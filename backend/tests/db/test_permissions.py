"""Real PostgreSQL privilege enforcement for incidentlens_app and tools_ro."""

import pytest
from psycopg import errors

pytestmark = pytest.mark.db

INPUT_TABLES = ["services", "scenarios", "logs", "metrics", "deployments", "doc_chunks"]
OUTPUT_TABLES = ["runs", "trace_steps", "eval_runs", "eval_results", "llm_cache"]
ACCOUNT_TABLES = ["users", "sessions"]
ADMIN_ONLY_TABLES = ["schema_migrations", "scenario_ground_truth"]
ALL_TABLES = INPUT_TABLES + OUTPUT_TABLES + ACCOUNT_TABLES + ADMIN_ONLY_TABLES
PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]

# Table-level privileges each role must have; everything else must be absent.
EXPECTED = {
    "incidentlens_app": {
        **{table: {"SELECT"} for table in INPUT_TABLES},
        # User incidents: evidence may be inserted (never updated or deleted); demos are
        # protected by a trigger. scenarios/services INSERT is column-level (see below).
        # Owners may delete their incidents and runs (the API checks ownership; triggers
        # protect demos); evidence and trace steps go with them through FK cascades.
        "scenarios": {"SELECT", "DELETE"},
        "logs": {"SELECT", "INSERT"},
        "metrics": {"SELECT", "INSERT"},
        "deployments": {"SELECT", "INSERT"},
        "runs": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "eval_runs": {"SELECT", "INSERT", "UPDATE"},
        "eval_results": {"SELECT", "INSERT", "UPDATE"},
        "trace_steps": {"SELECT", "INSERT"},
        "llm_cache": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        # Accounts are never updated or deleted by the API; sessions are deleted on logout/expiry.
        "users": {"SELECT", "INSERT"},
        "sessions": {"SELECT", "INSERT", "DELETE"},
    },
    "tools_ro": {table: {"SELECT"} for table in INPUT_TABLES},  # nothing on accounts or sessions
}


@pytest.mark.parametrize("role", ["incidentlens_app", "tools_ro"])
def test_privilege_matrix_is_exact(admin_conn, role):
    actual = {}
    for table in ALL_TABLES:
        granted = {
            privilege
            for privilege in PRIVILEGES
            if admin_conn.execute(
                "SELECT has_table_privilege(%s, %s, %s)", (role, table, privilege)
            ).fetchone()[0]
        }
        if granted:
            actual[table] = granted

    assert actual == EXPECTED[role]


COLUMN_INSERTS = {
    "incidentlens_app": {
        "scenarios": {"slug", "title", "alert_summary", "incident_start", "owner_id"},  # never 'kind'
        "services": {"name"},
    },
    "tools_ro": {},
}


@pytest.mark.parametrize("role", ["incidentlens_app", "tools_ro"])
@pytest.mark.parametrize("table", ["scenarios", "services"])
def test_column_level_insert_is_exact(admin_conn, role, table):
    columns = {
        name
        for (name,) in admin_conn.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0"
            " AND NOT attisdropped AND has_column_privilege(%s, %s, attname, 'INSERT')",
            (table, role, table),
        )
    }

    assert columns == COLUMN_INSERTS[role].get(table, set())


@pytest.mark.parametrize("role", ["incidentlens_app", "tools_ro"])
def test_no_column_level_access_to_ground_truth(admin_conn, role):
    has_any = admin_conn.execute(
        "SELECT has_any_column_privilege(%s, 'scenario_ground_truth', 'SELECT')", (role,)
    ).fetchone()[0]

    assert has_any is False


def test_public_has_no_database_or_schema_rights(admin_conn):
    connect, temp, usage = admin_conn.execute(
        "SELECT has_database_privilege('public', current_database(), 'CONNECT'),"
        " has_database_privilege('public', current_database(), 'TEMPORARY'),"
        " has_schema_privilege('public', 'public', 'USAGE')"
    ).fetchone()

    assert (connect, temp, usage) == (False, False, False)


# --- tools_ro: behaviour under real queries ---


@pytest.mark.parametrize("table", INPUT_TABLES)
def test_tools_ro_reads_input_tables(ro_conn, table):
    assert ro_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 1


GROUND_TRUTH_QUERIES = [
    "SELECT * FROM scenario_ground_truth",
    "SELECT expected_root_cause FROM scenario_ground_truth",
    "SELECT count(*) FROM scenario_ground_truth",
    "SELECT 1 FROM scenarios s JOIN scenario_ground_truth g ON g.scenario_id = s.id",
]


@pytest.mark.parametrize("query", GROUND_TRUTH_QUERIES, ids=["star", "root-cause", "count", "join"])
@pytest.mark.parametrize("conn_fixture", ["ro_conn", "app_conn"])
def test_application_roles_cannot_read_ground_truth(request, conn_fixture, query):
    conn = request.getfixturevalue(conn_fixture)

    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(query)


@pytest.mark.parametrize("table", OUTPUT_TABLES + ACCOUNT_TABLES + ADMIN_ONLY_TABLES)
def test_tools_ro_cannot_read_output_tables(ro_conn, table):
    with pytest.raises(errors.InsufficientPrivilege):
        ro_conn.execute(f"SELECT 1 FROM {table}")


WRITES = {
    "insert": "INSERT INTO {t} DEFAULT VALUES",
    "update": "UPDATE {t} SET id = DEFAULT",  # DEFAULT passes the identity-column rule
    "delete": "DELETE FROM {t}",
    "truncate": "TRUNCATE {t}",
}


@pytest.mark.parametrize("table", INPUT_TABLES)
@pytest.mark.parametrize("operation", WRITES)
def test_tools_ro_cannot_write_even_with_read_only_off(ro_conn, table, operation):
    # Turning off the read-only session default must not help: GRANTs are the real barrier.
    ro_conn.execute("SET default_transaction_read_only = off")
    ro_conn.execute("SET transaction_read_only = off")

    with pytest.raises(errors.InsufficientPrivilege):
        ro_conn.execute(WRITES[operation].format(t=table))


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE public.probe (id int)",
        "CREATE TEMP TABLE probe (id int)",
        "ALTER TABLE logs ADD COLUMN probe int",
        "DROP TABLE logs",
        "CREATE INDEX probe_idx ON logs (message)",
    ],
    ids=["create", "create-temp", "alter", "drop", "create-index"],
)
@pytest.mark.parametrize("conn_fixture", ["ro_conn", "app_conn"])
def test_no_ddl_for_application_roles(request, conn_fixture, statement):
    conn = request.getfixturevalue(conn_fixture)
    conn.execute("SET transaction_read_only = off")

    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement)


# --- incidentlens_app: allowed writes and denied writes ---


def test_app_can_record_a_run_and_trace(app_conn):
    run_id = app_conn.execute(
        "INSERT INTO runs (scenario_id, model)"
        " SELECT id, 'test-model' FROM scenarios WHERE slug = 'checkout-db-pool-exhaustion' RETURNING id"
    ).fetchone()[0]
    app_conn.execute(
        "INSERT INTO trace_steps (run_id, step_index, step_type, name) VALUES (%s, 0, 'tool_call', 'search_logs')",
        (run_id,),
    )
    app_conn.execute("UPDATE runs SET status = 'completed', finished_at = now() WHERE id = %s", (run_id,))

    assert app_conn.execute("SELECT count(*) FROM trace_steps WHERE run_id = %s", (run_id,)).fetchone()[0] == 1


def test_app_can_use_llm_cache(app_conn):
    key = "a" * 64
    app_conn.execute(
        "INSERT INTO llm_cache (cache_key, model, request, response) VALUES (%s, 'm', '{}', '{}')", (key,)
    )
    app_conn.execute("DELETE FROM llm_cache WHERE cache_key = %s", (key,))


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO scenarios (slug, title, alert_summary, incident_start, kind)"
        " VALUES ('u-forged', 't', 'a', now(), 'demo')",
        "INSERT INTO services (name, description) VALUES ('forged', 'x')",
        "UPDATE services SET description = 'x'",
        "UPDATE scenarios SET title = 'tampered'",
        "UPDATE scenarios SET owner_id = NULL",
        "UPDATE logs SET message = 'tampered'",
        "DELETE FROM logs",
        "DELETE FROM metrics",
        "TRUNCATE deployments",
        "UPDATE trace_steps SET name = 'tampered'",
        "DELETE FROM trace_steps",
        "TRUNCATE runs",
        "TRUNCATE scenarios",
        "TRUNCATE llm_cache",
        "SELECT 1 FROM schema_migrations",
        "UPDATE users SET password_hash = '$argon2id$forged'",
        "DELETE FROM users",
        "TRUNCATE sessions",
        "UPDATE sessions SET expires_at = now() + interval '10 years'",
    ],
    ids=[
        "forge-demo-kind", "service-description", "update-services", "update-scenarios",
        "reassign-owner", "update-logs", "delete-logs", "delete-metrics", "truncate-deployments",
        "update-trace", "delete-trace", "truncate-runs", "truncate-scenarios", "truncate-cache",
        "read-migrations", "update-users", "delete-users", "truncate-sessions", "extend-sessions",
    ],
)
def test_app_denied_outside_its_grants(app_conn, statement):
    with pytest.raises(errors.InsufficientPrivilege):
        app_conn.execute(statement)


DEMO = "(SELECT id FROM scenarios WHERE slug = 'checkout-db-pool-exhaustion')"
CHECKOUT_API = "(SELECT id FROM services WHERE name = 'checkout-api')"


@pytest.mark.parametrize(
    "statement",
    [
        f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message)"
        f" VALUES ({DEMO}, {CHECKOUT_API}, 'log:9999', now(), 'ERROR', 'planted')",
        f"INSERT INTO metrics (scenario_id, service_id, name, ts, value) VALUES ({DEMO}, {CHECKOUT_API}, 'x', now(), 1)",
        f"INSERT INTO deployments (scenario_id, service_id, ref, version, deployed_at, status)"
        f" VALUES ({DEMO}, {CHECKOUT_API}, 'deploy:99', 'v9', now(), 'succeeded')",
    ],
    ids=["logs", "metrics", "deployments"],
)
def test_app_cannot_write_into_demo_incidents(app_conn, statement):
    with pytest.raises(errors.InsufficientPrivilege, match="demo incidents are read-only"):
        app_conn.execute(statement)


def test_owner_can_still_reseed_demos(admin_conn):
    admin_conn.execute(
        f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message)"
        f" VALUES ({DEMO}, {CHECKOUT_API}, 'log:9999', now(), 'INFO', 'owner write')"
    )  # rolled back by the fixture


@pytest.mark.parametrize(
    "statement",
    [f"DELETE FROM scenarios WHERE id = {DEMO}", f"DELETE FROM runs WHERE scenario_id = {DEMO}"],
    ids=["demo-incident", "demo-runs"],
)
def test_app_cannot_delete_demo_data(app_conn, admin_conn, statement):
    admin_conn.execute(f"INSERT INTO runs (scenario_id, model) VALUES ({DEMO}, 'm')")
    admin_conn.commit()
    try:
        with pytest.raises(errors.InsufficientPrivilege, match="demo incidents are read-only"):
            app_conn.execute(statement)
    finally:
        admin_conn.execute(f"DELETE FROM runs WHERE scenario_id = {DEMO} AND model = 'm'")
        admin_conn.commit()


def test_app_can_delete_a_user_incident_with_everything_in_it(app_conn):
    owner = app_conn.execute(
        "INSERT INTO users (email, password_hash) VALUES ('perm-delete@example.com', '$argon2id$x') RETURNING id"
    ).fetchone()[0]
    scenario_id = app_conn.execute(
        "INSERT INTO scenarios (slug, title, alert_summary, incident_start, owner_id)"
        " VALUES ('u-perm-delete', 't', 'a', now(), %s) RETURNING id", (owner,)
    ).fetchone()[0]
    app_conn.execute(
        f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message)"
        f" VALUES (%s, {CHECKOUT_API}, 'log:0001', now(), 'ERROR', 'x')", (scenario_id,)
    )
    run_id = app_conn.execute("INSERT INTO runs (scenario_id, model) VALUES (%s, 'm') RETURNING id",
                              (scenario_id,)).fetchone()[0]
    app_conn.execute("INSERT INTO trace_steps (run_id, step_index, step_type, name) VALUES (%s, 0, 'llm_call', 'x')",
                     (run_id,))

    app_conn.execute("DELETE FROM runs WHERE scenario_id = %s", (scenario_id,))
    app_conn.execute("DELETE FROM scenarios WHERE id = %s", (scenario_id,))

    assert app_conn.execute("SELECT count(*) FROM logs WHERE scenario_id = %s", (scenario_id,)).fetchone()[0] == 0
    assert app_conn.execute("SELECT count(*) FROM trace_steps WHERE run_id = %s", (run_id,)).fetchone()[0] == 0
