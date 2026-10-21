"""Real PostgreSQL privilege enforcement for incidentlens_app and tools_ro."""

import pytest
from psycopg import errors

pytestmark = pytest.mark.db

INPUT_TABLES = ["services", "scenarios", "logs", "metrics", "deployments", "doc_chunks"]
OUTPUT_TABLES = ["runs", "trace_steps", "eval_runs", "eval_results", "llm_cache"]
ADMIN_ONLY_TABLES = ["schema_migrations", "scenario_ground_truth"]
ALL_TABLES = INPUT_TABLES + OUTPUT_TABLES + ADMIN_ONLY_TABLES
PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]

# Table-level privileges each role must have; everything else must be absent.
EXPECTED = {
    "incidentlens_app": {
        **{table: {"SELECT"} for table in INPUT_TABLES},
        "runs": {"SELECT", "INSERT", "UPDATE"},
        "eval_runs": {"SELECT", "INSERT", "UPDATE"},
        "eval_results": {"SELECT", "INSERT", "UPDATE"},
        "trace_steps": {"SELECT", "INSERT"},
        "llm_cache": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    },
    "tools_ro": {table: {"SELECT"} for table in INPUT_TABLES},
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


@pytest.mark.parametrize("table", OUTPUT_TABLES + ADMIN_ONLY_TABLES)
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
        "INSERT INTO logs DEFAULT VALUES",
        "UPDATE services SET description = 'x'",
        "DELETE FROM scenarios",
        "UPDATE trace_steps SET name = 'tampered'",
        "DELETE FROM trace_steps",
        "DELETE FROM runs",
        "TRUNCATE llm_cache",
        "SELECT 1 FROM schema_migrations",
    ],
    ids=[
        "insert-logs", "update-services", "delete-scenarios", "update-trace",
        "delete-trace", "delete-runs", "truncate-cache", "read-migrations",
    ],
)
def test_app_denied_outside_its_grants(app_conn, statement):
    with pytest.raises(errors.InsufficientPrivilege):
        app_conn.execute(statement)
