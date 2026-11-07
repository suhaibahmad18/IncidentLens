"""Investigation run lifecycle across restarts and overlapping app instances (throwaway test DB).

The deployment-overlap tests start a real "old" app instance whose model call is held open,
then start a "new" instance against the same database, exactly like a rolling deploy.
"""

import logging
import threading
import time
import uuid

import pytest

from app.db.connection import connect
from app.runs import store
from app.runs.service import stale_run_seconds
from tests.db.test_agent import GATHER, report_citing_seen
from tests.db.test_api import make_client
from tests.db.test_user_incidents import new_incident

pytestmark = pytest.mark.db

SLUG = "checkout-db-pool-exhaustion"
STALE = 750  # the default threshold (see tests/test_run_staleness.py)
LONG_AGO = 3600  # seconds; far beyond the threshold


def add_run(conn, status="running", started_ago=0, trace_ago=None, slug=SLUG):
    run_id = conn.execute(
        "INSERT INTO runs (scenario_id, model, status, started_at)"
        " SELECT id, 'lifecycle-test', %s, now() - make_interval(secs => %s) FROM scenarios WHERE slug = %s"
        " RETURNING id",
        (status, started_ago, slug),
    ).fetchone()[0]
    if trace_ago is not None:
        conn.execute(
            "INSERT INTO trace_steps (run_id, step_index, step_type, name, created_at)"
            " VALUES (%s, 0, 'llm_call', 'm', now() - make_interval(secs => %s))",
            (run_id, trace_ago),
        )
    return run_id


def run_row(conn, run_id):
    return conn.execute("SELECT status, error, final_report FROM runs WHERE id = %s", (run_id,)).fetchone()


# --- store level (one transaction, rolled back) ---


def test_normal_completion_is_stored(app_conn):
    run_id = add_run(app_conn, status="pending")
    store.mark_running(app_conn, run_id)

    assert store.finish_run(app_conn, run_id, {"status": "conclusive"}, None) is True
    status, error, report = run_row(app_conn, run_id)
    assert (status, error, report) == ("completed", None, {"status": "conclusive"})


def test_normal_failure_is_stored(app_conn):
    run_id = add_run(app_conn)

    assert store.finish_run(app_conn, run_id, None, "timeout: The language model did not respond in time.") is True
    assert run_row(app_conn, run_id)[:2] == ("failed", "timeout: The language model did not respond in time.")


@pytest.mark.parametrize("status", ["pending", "running"])
def test_stale_unfinished_run_is_marked_interrupted(app_conn, status):
    run_id = add_run(app_conn, status=status, started_ago=LONG_AGO)

    assert store.fail_interrupted_runs(app_conn, STALE) >= 1
    assert run_row(app_conn, run_id)[:2] == ("failed", store.INTERRUPTED_ERROR)


def test_running_run_with_old_progress_is_marked_interrupted(app_conn):
    run_id = add_run(app_conn, started_ago=LONG_AGO, trace_ago=STALE + 60)

    store.fail_interrupted_runs(app_conn, STALE)
    assert run_row(app_conn, run_id)[0] == "failed"


@pytest.mark.parametrize(
    ("started_ago", "trace_ago"),
    [(0, None), (STALE - 60, None), (LONG_AGO, 30), (LONG_AGO, STALE - 60)],
    ids=["just-started", "quiet-but-within-threshold", "long-run-recent-progress", "slow-model-call"],
)
def test_run_that_may_still_be_alive_is_left_alone(app_conn, started_ago, trace_ago):
    run_id = add_run(app_conn, started_ago=started_ago, trace_ago=trace_ago)

    store.fail_interrupted_runs(app_conn, STALE)
    assert run_row(app_conn, run_id)[0] == "running"


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_terminal_status_is_never_overwritten(app_conn, terminal):
    run_id = add_run(app_conn, status=terminal, started_ago=LONG_AGO)
    app_conn.execute("UPDATE runs SET error = 'original', finished_at = now() WHERE id = %s", (run_id,))

    store.fail_interrupted_runs(app_conn, STALE)
    assert run_row(app_conn, run_id) == (terminal, "original", None)
    assert store.finish_run(app_conn, run_id, {"late": True}, None) is False
    assert store.finish_run(app_conn, run_id, None, "late failure") is False
    assert run_row(app_conn, run_id) == (terminal, "original", None)


def test_late_finish_after_interruption_is_rejected(app_conn):
    run_id = add_run(app_conn, started_ago=LONG_AGO)
    store.fail_interrupted_runs(app_conn, STALE)

    assert store.finish_run(app_conn, run_id, {"status": "conclusive"}, None) is False
    assert run_row(app_conn, run_id) == ("failed", store.INTERRUPTED_ERROR, None)


# --- two real connections racing on the same run (committed rows, cleaned up) ---


@pytest.fixture
def committed_run(test_db):
    with connect(test_db["admin"], autocommit=True) as conn:
        run_id = add_run(conn, started_ago=LONG_AGO)
    yield run_id
    with connect(test_db["admin"], autocommit=True) as conn:
        conn.execute("DELETE FROM runs WHERE id = %s", (run_id,))


def race(test_db, first, second):
    """`first` writes in an open transaction; `second` runs meanwhile in another connection.
    Returns (first result, second result) after `first` commits."""
    results = {}
    with connect(test_db["app"]) as holder:
        results["first"] = first(holder)  # row now locked by the open transaction

        def other():
            with connect(test_db["app"], autocommit=True) as conn:
                results["second"] = second(conn)

        thread = threading.Thread(target=other)
        thread.start()
        time.sleep(0.5)
        assert thread.is_alive(), "the second writer should wait for the first writer's row lock"
        holder.commit()
    thread.join(10)
    assert not thread.is_alive()
    return results["first"], results["second"]


def test_finish_wins_over_a_concurrent_sweep(test_db, committed_run):
    finished, swept = race(test_db,
                           lambda c: store.finish_run(c, committed_run, {"status": "conclusive"}, None),
                           lambda c: store.fail_interrupted_runs(c, STALE))
    assert (finished, swept) == (True, 0)
    with connect(test_db["admin"]) as conn:
        assert run_row(conn, committed_run)[0] == "completed"


def test_sweep_wins_over_a_concurrent_finish(test_db, committed_run):
    swept, finished = race(test_db,
                           lambda c: store.fail_interrupted_runs(c, STALE),
                           lambda c: store.finish_run(c, committed_run, {"status": "conclusive"}, None))
    assert swept >= 1 and finished is False
    with connect(test_db["admin"]) as conn:
        assert run_row(conn, committed_run) == ("failed", store.INTERRUPTED_ERROR, None)


# --- deployment overlap: two app instances, one database ---


class HeldModelCall:
    """First model call blocks until released, like a slow LLM request on the old instance."""

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()

    def __call__(self, messages):
        self.started.set()
        assert self.release.wait(60), "test never released the held model call"
        return GATHER


def start_old_instance(test_db, held):
    def run():
        with make_client(test_db, [held, report_citing_seen]) as old:
            old.post(f"/api/incidents/{SLUG}/investigations")  # returns after the background run ends

    thread = threading.Thread(target=run)
    thread.start()
    assert held.started.wait(30), "old instance never reached the model call"
    with connect(test_db["admin"]) as conn:
        run_id = conn.execute(
            "SELECT id FROM runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1").fetchone()[0]
    return thread, run_id


def test_overlapping_deploy_does_not_fail_a_live_run(test_db):
    held = HeldModelCall()
    old_thread, run_id = start_old_instance(test_db, held)
    try:
        with make_client(test_db) as new:  # new instance starts while the old one is mid-run
            assert new.get(f"/api/runs/{run_id}").json()["status"] == "running"
    finally:
        held.release.set()
        old_thread.join(60)

    with make_client(test_db) as new:
        run = new.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed" and run["error"] is None and run["report"]  # never running -> failed -> completed


def test_interrupted_run_is_not_resurrected_by_the_old_instance(test_db, caplog):
    held = HeldModelCall()
    old_thread, run_id = start_old_instance(test_db, held)
    try:
        # Simulate an old instance that has made no progress for far longer than any live run can.
        with connect(test_db["admin"], autocommit=True) as conn:
            conn.execute("UPDATE runs SET started_at = now() - make_interval(secs => %s) WHERE id = %s",
                         (LONG_AGO, run_id))
        with make_client(test_db) as new:
            failed = new.get(f"/api/runs/{run_id}").json()
        assert (failed["status"], failed["error"]) == ("failed", store.INTERRUPTED_ERROR)
    finally:
        with caplog.at_level(logging.WARNING, logger="incidentlens.runs"):
            held.release.set()  # the old instance now finishes and tries to store its report
            old_thread.join(60)

    with make_client(test_db) as new:
        final = new.get(f"/api/runs/{run_id}").json()
    assert (final["status"], final["error"], final["report"]) == ("failed", store.INTERRUPTED_ERROR, None)
    assert any("already finished" in record.getMessage() for record in caplog.records)


def test_starting_an_investigation_retires_crashed_runs(test_db):
    with make_client(test_db, [GATHER, report_citing_seen]) as client:
        with connect(test_db["admin"], autocommit=True) as conn:  # crashed after this instance started
            crashed = add_run(conn, started_ago=LONG_AGO)
        assert client.get(f"/api/runs/{crashed}").json()["status"] == "running"

        assert client.post(f"/api/incidents/{SLUG}/investigations").status_code == 202
        assert client.get(f"/api/runs/{crashed}").json()["status"] == "failed"


def test_crashed_run_no_longer_blocks_deleting_its_incident(test_db):
    with make_client(test_db, signed_in=True) as owner:
        slug = owner.post("/api/incidents", json=new_incident(incident_start="2026-08-20T14:00:00Z")).json()["slug"]
        with connect(test_db["admin"], autocommit=True) as conn:
            live = add_run(conn, slug=slug)
        assert owner.delete(f"/api/incidents/{slug}").status_code == 409  # may still be running: refused

        with connect(test_db["admin"], autocommit=True) as conn:
            conn.execute("UPDATE runs SET started_at = now() - make_interval(secs => %s) WHERE id = %s",
                         (LONG_AGO, live))
        assert owner.delete(f"/api/incidents/{slug}").status_code == 204  # crashed long ago: retired, deleted


def test_threshold_used_by_the_app_is_the_default(test_db):
    with make_client(test_db) as client:
        assert stale_run_seconds(client.app.state.settings) == STALE


def test_unknown_run_id_cannot_be_finished(app_conn):
    assert store.finish_run(app_conn, uuid.uuid4(), None, None) is False
