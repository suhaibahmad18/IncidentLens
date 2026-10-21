import shutil

import pytest

from app.db.migrate import MIGRATIONS_DIR, MigrationError, run_migrations

pytestmark = pytest.mark.db

EXPECTED_TABLES = {
    "services", "scenarios", "logs", "metrics", "deployments", "doc_chunks",
    "runs", "trace_steps", "eval_runs", "eval_results", "llm_cache", "schema_migrations",
    "scenario_ground_truth",
}


def test_all_tables_exist(admin_conn):
    rows = admin_conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    ).fetchall()

    assert {row[0] for row in rows} == EXPECTED_TABLES


def test_embedding_is_384_dimensional_vector(admin_conn):
    column_type = admin_conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
        " WHERE attrelid = 'doc_chunks'::regclass AND attname = 'embedding'"
    ).fetchone()[0]

    assert column_type == "vector(384)"


@pytest.mark.parametrize(
    ("table", "column", "expected"),
    [
        ("runs", "id", "uuid"),
        ("logs", "ts", "timestamp with time zone"),
        ("logs", "attributes", "jsonb"),
        ("metrics", "value", "double precision"),
        ("eval_results", "score", "double precision"),
    ],
)
def test_key_column_types(admin_conn, table, column, expected):
    actual = admin_conn.execute(
        "SELECT data_type FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()[0]

    assert actual == expected


def test_migrations_recorded(admin_conn):
    versions = [row[0] for row in admin_conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]

    assert versions == ["0001_initial_schema", "0002_grants", "0003_ground_truth_and_evidence_refs"]


def test_scenarios_table_holds_no_ground_truth(admin_conn):
    columns = {
        row[0]
        for row in admin_conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'scenarios'"
        )
    }

    assert columns == {"id", "slug", "title", "alert_summary", "incident_start", "created_at"}


def test_rerunning_migrations_applies_nothing(test_db):
    assert run_migrations(test_db["admin"]) == []


def test_edited_applied_migration_aborts(test_db, tmp_path):
    for path in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(path, tmp_path / path.name)
    edited = tmp_path / "0001_initial_schema.sql"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="0001_initial_schema was edited"):
        run_migrations(test_db["admin"], directory=tmp_path)
