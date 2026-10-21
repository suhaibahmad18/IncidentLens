from app.eval.scoring import GroundTruth, score_report

TRUTH = GroundTruth(
    slug="checkout-db-pool-exhaustion", split="dev", expected_root_cause="...", service="payments-api",
    keywords=[["connection", "pool"], ["v2.14", "deploy", "leak"]],
    key_evidence_ids=["deploy:2", "log:0010", "metric:payments-db:db_connections_in_use"],
)


def report(**overrides):
    return {
        "status": "conclusive",
        "root_cause": "payments-api v2.14.0 leaks connections and exhausts the pool",
        "explanation": "",
        "affected_service": "payments-api",
        "evidence": [{"evidence_id": "deploy:2"}, {"evidence_id": "log:0010"}],
        "dropped_citations": [],
        **overrides,
    }


def test_correct_report_scores_high():
    s = score_report(report(), TRUTH)

    assert s.root_cause_correct and s.service_correct and not s.unsupported_claim
    assert s.keyword_groups_matched == 2
    assert s.key_evidence_recall == round(2 / 3, 3)
    assert s.score == round(0.5 + 0.2 + 0.2 * (2 / 3) + 0.1, 3)


def test_wrong_service_is_not_correct():
    s = score_report(report(affected_service="payments-db"), TRUTH)

    assert not s.root_cause_correct and not s.service_correct


def test_missing_keyword_group_is_not_correct():
    s = score_report(report(root_cause="payments-api has a connection problem"), TRUTH)

    assert s.keyword_groups_matched == 1 and not s.root_cause_correct


def test_inconclusive_is_never_counted_correct():
    s = score_report(report(status="inconclusive"), TRUTH)

    assert not s.root_cause_correct and not s.unsupported_claim


def test_conclusive_without_valid_citations_is_unsupported():
    assert score_report(report(evidence=[]), TRUTH).unsupported_claim
    assert score_report(report(dropped_citations=["log:9999"]), TRUTH).unsupported_claim


def test_failed_run_scores_zero():
    s = score_report(None, TRUTH)

    assert not s.completed and s.score == 0.0
