"""Constraint behaviour, exercised as the table owner. Every test's writes are rolled back."""

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.db

SCENARIO = "(SELECT id FROM scenarios WHERE slug = 'test-pool-exhaustion')"
SERVICE = "(SELECT id FROM services WHERE name = 'checkout-api')"


def test_log_with_unknown_scenario_is_rejected(admin_conn):
    with pytest.raises(errors.ForeignKeyViolation):
        admin_conn.execute(
            f"INSERT INTO logs (scenario_id, service_id, ts, level, message)"
            f" VALUES (999999, {SERVICE}, now(), 'INFO', 'x')"
        )


@pytest.mark.parametrize(
    "statement",
    [
        f"INSERT INTO logs (scenario_id, service_id, ts, level, message) VALUES ({SCENARIO}, {SERVICE}, now(), 'LOUD', 'x')",
        f"INSERT INTO deployments (scenario_id, service_id, version, deployed_at, status) VALUES ({SCENARIO}, {SERVICE}, 'v1', now(), 'maybe')",
        f"INSERT INTO runs (scenario_id, model, status) VALUES ({SCENARIO}, 'm', 'paused')",
        f"INSERT INTO runs (scenario_id, model, started_at, finished_at) VALUES ({SCENARIO}, 'm', now(), now() - interval '1 hour')",
        "INSERT INTO llm_cache (cache_key, model, request, response) VALUES ('not-a-sha', 'm', '{}', '{}')",
        "INSERT INTO services (name) VALUES ('')",
    ],
    ids=["log-level", "deployment-status", "run-status", "finished-before-started", "cache-key", "empty-service-name"],
)
def test_check_constraints(admin_conn, statement):
    with pytest.raises(errors.CheckViolation):
        admin_conn.execute(statement)


def test_eval_score_must_be_between_0_and_1(admin_conn):
    eval_run_id = admin_conn.execute(
        "INSERT INTO eval_runs (model) VALUES ('m') RETURNING id"
    ).fetchone()[0]

    with pytest.raises(errors.CheckViolation):
        admin_conn.execute(
            f"INSERT INTO eval_results (eval_run_id, scenario_id, root_cause_correct, score)"
            f" VALUES (%s, {SCENARIO}, true, 1.5)",
            (eval_run_id,),
        )


def test_trace_step_index_unique_per_run(admin_conn):
    run_id = admin_conn.execute(
        f"INSERT INTO runs (scenario_id, model) VALUES ({SCENARIO}, 'm') RETURNING id"
    ).fetchone()[0]
    insert = "INSERT INTO trace_steps (run_id, step_index, step_type) VALUES (%s, 0, 'llm_call')"
    admin_conn.execute(insert, (run_id,))

    with pytest.raises(errors.UniqueViolation):
        admin_conn.execute(insert, (run_id,))


def test_doc_chunk_source_and_index_unique(admin_conn):
    with pytest.raises(errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO doc_chunks (source, chunk_index, content) VALUES ('runbooks/test.md', 0, 'dup')"
        )


def test_wrong_embedding_dimension_is_rejected(admin_conn):
    with pytest.raises(psycopg.errors.DataException):
        admin_conn.execute(
            "INSERT INTO doc_chunks (source, chunk_index, content, embedding)"
            " VALUES ('runbooks/x.md', 0, 'x', '[1,2,3]')"
        )


def test_deleting_scenario_cascades_to_telemetry(admin_conn):
    admin_conn.execute(f"DELETE FROM scenarios WHERE id = {SCENARIO}")

    for table in ("logs", "metrics", "deployments"):
        assert admin_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_deleting_scenario_with_runs_is_blocked(admin_conn):
    admin_conn.execute(f"INSERT INTO runs (scenario_id, model) VALUES ({SCENARIO}, 'm')")

    with pytest.raises(errors.ForeignKeyViolation):
        admin_conn.execute(f"DELETE FROM scenarios WHERE id = {SCENARIO}")
