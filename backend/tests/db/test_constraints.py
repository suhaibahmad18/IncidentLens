"""Constraint behaviour, exercised as the table owner. Every test's writes are rolled back."""

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.db

SCENARIO = "(SELECT id FROM scenarios WHERE slug = 'checkout-db-pool-exhaustion')"
SERVICE = "(SELECT id FROM services WHERE name = 'checkout-api')"


def test_log_with_unknown_scenario_is_rejected(admin_conn):
    with pytest.raises(errors.ForeignKeyViolation):
        admin_conn.execute(
            f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message)"
            f" VALUES (999999, {SERVICE}, 'log:9999', now(), 'INFO', 'x')"
        )


@pytest.mark.parametrize(
    "statement",
    [
        f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message) VALUES ({SCENARIO}, {SERVICE}, 'log:9999', now(), 'LOUD', 'x')",
        f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message) VALUES ({SCENARIO}, {SERVICE}, 'L-1', now(), 'INFO', 'x')",
        f"INSERT INTO deployments (scenario_id, service_id, ref, version, deployed_at, status) VALUES ({SCENARIO}, {SERVICE}, 'deploy:99', 'v1', now(), 'maybe')",
        f"INSERT INTO runs (scenario_id, model, status) VALUES ({SCENARIO}, 'm', 'paused')",
        f"INSERT INTO runs (scenario_id, model, started_at, finished_at) VALUES ({SCENARIO}, 'm', now(), now() - interval '1 hour')",
        "INSERT INTO llm_cache (cache_key, model, request, response) VALUES ('not-a-sha', 'm', '{}', '{}')",
        "INSERT INTO services (name) VALUES ('')",
        f"INSERT INTO scenario_ground_truth (scenario_id, split, expected_root_cause, root_cause_service_id) VALUES ({SCENARIO}, 'train', 'x', {SERVICE})",
    ],
    ids=["log-level", "log-ref-format", "deployment-status", "run-status", "finished-before-started",
         "cache-key", "empty-service-name", "ground-truth-split"],
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


def test_log_ref_unique_per_scenario(admin_conn):
    with pytest.raises(errors.UniqueViolation):
        admin_conn.execute(
            f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message)"
            f" VALUES ({SCENARIO}, {SERVICE}, 'log:0001', now(), 'INFO', 'dup')"
        )


def test_doc_chunk_source_and_index_unique(admin_conn):
    with pytest.raises(errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO doc_chunks (source, chunk_index, content)"
            " VALUES ('runbooks/general-incident-triage.md', 0, 'dup')"
        )


def test_wrong_embedding_dimension_is_rejected(admin_conn):
    with pytest.raises(psycopg.errors.DataException):
        admin_conn.execute(
            "INSERT INTO doc_chunks (source, chunk_index, content, embedding)"
            " VALUES ('runbooks/x.md', 0, 'x', '[1,2,3]')"
        )


def test_deleting_scenario_cascades_to_telemetry_and_ground_truth(admin_conn):
    # A throwaway scenario: seeded ones may have committed runs, which (correctly) block deletion.
    scenario_id = admin_conn.execute(
        "INSERT INTO scenarios (slug, title, alert_summary, incident_start, kind)"
        " VALUES ('cascade-probe', 't', 'a', now(), 'demo') RETURNING id"
    ).fetchone()[0]
    for statement in (
        f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message) VALUES (%s, {SERVICE}, 'log:0001', now(), 'INFO', 'x')",
        f"INSERT INTO metrics (scenario_id, service_id, name, ts, value) VALUES (%s, {SERVICE}, 'm', now(), 1)",
        f"INSERT INTO deployments (scenario_id, service_id, ref, version, deployed_at, status) VALUES (%s, {SERVICE}, 'deploy:1', 'v1', now(), 'succeeded')",
        f"INSERT INTO scenario_ground_truth (scenario_id, split, expected_root_cause, root_cause_service_id) VALUES (%s, 'dev', 'x', {SERVICE})",
    ):
        admin_conn.execute(statement, (scenario_id,))

    admin_conn.execute("DELETE FROM scenarios WHERE id = %s", (scenario_id,))

    for table in ("logs", "metrics", "deployments", "scenario_ground_truth"):
        count = admin_conn.execute(
            f"SELECT count(*) FROM {table} WHERE scenario_id = %s", (scenario_id,)
        ).fetchone()[0]
        assert count == 0


def test_deleting_scenario_with_runs_is_blocked(admin_conn):
    admin_conn.execute(f"INSERT INTO runs (scenario_id, model) VALUES ({SCENARIO}, 'm')")

    with pytest.raises(errors.ForeignKeyViolation):
        admin_conn.execute(f"DELETE FROM scenarios WHERE id = {SCENARIO}")


def _user(admin_conn, email: str) -> int:
    return admin_conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (%s, '$argon2id$v=19$m=19456,t=2,p=1$x$y') RETURNING id",
        (email,),
    ).fetchone()[0]


@pytest.mark.parametrize(
    "statement",
    [
        # A user incident must have an owner; a demo must not.
        "INSERT INTO scenarios (slug, title, alert_summary, incident_start) VALUES ('orphan', 't', 'a', now())",
        "INSERT INTO scenarios (slug, title, alert_summary, incident_start, kind, owner_id)"
        " VALUES ('owned-demo', 't', 'a', now(), 'demo', {owner})",
    ],
)
def test_ownership_matches_kind(admin_conn, statement):
    owner = _user(admin_conn, "constraint-owner@example.com")
    with pytest.raises(errors.CheckViolation):
        admin_conn.execute(statement.format(owner=owner))


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        ("INSERT INTO users (email, password_hash) VALUES ('Mixed@Example.com', '$argon2id$x')", errors.CheckViolation),
        ("INSERT INTO users (email, password_hash) VALUES ('plain@example.com', 'hunter2hunter2')", errors.CheckViolation),
        ("INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at)"
         " VALUES ('not-a-hash', {owner}, repeat('c', 43), now() + interval '1 hour')", errors.CheckViolation),
        ("INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at)"
         " VALUES (repeat('a', 64), {owner}, repeat('c', 43), now() - interval '1 hour')", errors.CheckViolation),
        ("INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at)"
         " VALUES (repeat('a', 64), 999999999, repeat('c', 43), now() + interval '1 hour')", errors.ForeignKeyViolation),
    ],
)
def test_account_constraints(admin_conn, statement, error):
    owner = _user(admin_conn, "constraint-sessions@example.com")
    with pytest.raises(error):
        admin_conn.execute(statement.format(owner=owner))


def test_deleting_an_account_removes_its_sessions_and_incidents(admin_conn):
    owner = _user(admin_conn, "constraint-cascade@example.com")
    scenario_id = admin_conn.execute(
        "INSERT INTO scenarios (slug, title, alert_summary, incident_start, owner_id)"
        " VALUES ('owned-probe', 't', 'a', now(), %s) RETURNING id", (owner,)
    ).fetchone()[0]
    admin_conn.execute(
        f"INSERT INTO logs (scenario_id, service_id, ref, ts, level, message) VALUES (%s, {SERVICE}, 'log:0001', now(), 'INFO', 'x')",
        (scenario_id,),
    )
    admin_conn.execute(
        "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at)"
        " VALUES (repeat('b', 64), %s, repeat('c', 43), now() + interval '1 hour')", (owner,)
    )

    admin_conn.execute("DELETE FROM users WHERE id = %s", (owner,))

    for sql in ("SELECT count(*) FROM scenarios WHERE id = %s", "SELECT count(*) FROM logs WHERE scenario_id = %s"):
        assert admin_conn.execute(sql, (scenario_id,)).fetchone()[0] == 0
    assert admin_conn.execute("SELECT count(*) FROM sessions WHERE user_id = %s", (owner,)).fetchone()[0] == 0
