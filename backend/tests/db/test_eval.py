"""The evaluation harness end to end, with a scripted model that is right for one scenario only."""

import pytest

from app.config import Settings
from app.eval.run import format_results, run_evaluation
from tests.db.test_agent import GATHER, report_citing_seen
from tests.fakes import FakeEmbedder
from tests.llm_fakes import ScriptedLLM

pytestmark = pytest.mark.db


def test_evaluation_scores_every_scenario_and_records_results(test_db, admin_conn):
    settings = Settings(_env_file=None, database_url=test_db["app"], tools_ro_database_url=test_db["tools_ro"],
                        llm_cache_enabled=False, groq_model="scripted-test-model")

    eval_run_id, results = run_evaluation(
        settings, test_db["admin"], lambda: ScriptedLLM([GATHER, report_citing_seen]), FakeEmbedder()
    )

    by_slug = {truth.slug: score for truth, score, _ in results}
    assert set(by_slug) == {"checkout-db-pool-exhaustion", "auth-certificate-expiry",
                            "inventory-memory-leak", "notification-email-backlog"}
    # The scripted answer always blames payments-api's connection pool:
    assert by_slug["checkout-db-pool-exhaustion"].root_cause_correct is True
    assert by_slug["auth-certificate-expiry"].root_cause_correct is False
    assert by_slug["notification-email-backlog"].root_cause_correct is False
    assert {truth.split for truth, _, _ in results} == {"dev", "holdout"}

    status, stored = admin_conn.execute(
        "SELECT r.status, count(x.*) FROM eval_runs r JOIN eval_results x ON x.eval_run_id = r.id"
        " WHERE r.id = %s GROUP BY r.status", (eval_run_id,)
    ).fetchone()
    assert (status, stored) == ("completed", 4)

    table = format_results(results)
    assert "dev: root cause correct 1/3" in table
    assert "holdout: root cause correct 0/1" in table


def test_split_filter_runs_only_holdout(test_db):
    settings = Settings(_env_file=None, database_url=test_db["app"], tools_ro_database_url=test_db["tools_ro"],
                        llm_cache_enabled=False)

    _, results = run_evaluation(settings, test_db["admin"], lambda: ScriptedLLM([GATHER, report_citing_seen]),
                                FakeEmbedder(), split="holdout")

    assert [truth.slug for truth, _, _ in results] == ["notification-email-backlog"]
