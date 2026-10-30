"""Offline: report parsing, citation validation and result size bounds."""

import json

import pytest

from app.agent.loop import fit_for_model, finalize_report
from app.agent.report import Report, ReportFormatError, parse_report

SERVICES = ("payments-api", "payments-db")

GOOD = {
    "status": "conclusive",
    "facts": [{"statement": "payments-api v2.14.0 was deployed 20 minutes before", "evidence_ids": ["deploy:2"]}],
    "root_cause": "payments-api v2.14.0 leaks DB connections",
    "affected_service": "payments-api",
    "confidence": 0.85,
    "explanation": "Connections climbed right after deploy:2.",
    "evidence": [{"evidence_id": "deploy:2", "why": "deployed at -20m"}, {"evidence_id": "log:0007", "why": "pool timeout"}],
    "remediation": ["Roll back payments-api to v2.13.x (requires approval)"],
    "missing_information": [],
}


@pytest.mark.parametrize(
    "content",
    [
        json.dumps(GOOD),
        f"```json\n{json.dumps(GOOD)}\n```",
        f"Here is the report:\n{json.dumps(GOOD)}\nThanks.",
    ],
    ids=["plain", "fenced", "with-prose"],
)
def test_parse_report_accepts_common_formats(content):
    assert parse_report(content).affected_service == "payments-api"


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (None, "empty"),
        ("no json here", "did not contain a JSON object"),
        ('{"status": "conclusive", "root_cause": ', "did not contain a JSON object"),
        ('{"status": "maybe"}', "status"),
        (json.dumps({**GOOD, "confidence": 1.7}), "confidence"),
        ('{"status": "conclusive", oops}', "invalid JSON"),
    ],
    ids=["none", "prose", "truncated", "bad-status", "confidence-range", "broken-json"],
)
def test_parse_report_rejects_invalid_answers(content, problem):
    with pytest.raises(ReportFormatError, match=problem):
        parse_report(content)


def test_finalize_keeps_only_citations_tools_returned():
    report = finalize_report(Report(**GOOD), {"deploy:2"}, SERVICES)

    assert [c["evidence_id"] for c in report["evidence"]] == ["deploy:2"]
    assert report["dropped_citations"] == ["log:0007"]
    assert report["status"] == "conclusive"
    assert report["advisory_only"] is True


def test_finalize_downgrades_conclusion_without_valid_evidence():
    report = finalize_report(Report(**GOOD), set(), SERVICES)

    assert report["status"] == "inconclusive"
    assert report["confidence"] <= 0.4
    assert report["evidence"] == []
    assert report["validation_notes"]


def test_finalize_rejects_unknown_service():
    report = finalize_report(Report(**{**GOOD, "affected_service": "mystery-svc"}), {"deploy:2"}, SERVICES)

    assert report["affected_service"] is None
    assert report["status"] == "inconclusive"


def test_inconclusive_confidence_is_capped():
    report = finalize_report(Report(**{**GOOD, "status": "inconclusive", "confidence": 0.9}), {"deploy:2"}, SERVICES)

    assert report["confidence"] == 0.4


# --- facts vs hypothesis ---


def test_facts_with_valid_evidence_are_kept_separately_from_the_hypothesis():
    report = finalize_report(Report(**GOOD), {"deploy:2", "log:0007"}, SERVICES)

    assert report["facts"] == [{"statement": "payments-api v2.14.0 was deployed 20 minutes before",
                                "evidence_ids": ["deploy:2"]}]
    assert report["root_cause"] == GOOD["root_cause"]  # hypothesis stays its own field
    assert report["unsupported_statements"] == []
    assert report["status"] == "conclusive"


def test_fabricated_fact_references_are_removed():
    facts = [{"statement": "pool timeouts started at 13:58", "evidence_ids": ["log:0007", "log:4242", "made-up"]}]
    report = finalize_report(Report(**{**GOOD, "facts": facts}), {"deploy:2", "log:0007"}, SERVICES)

    assert report["facts"][0]["evidence_ids"] == ["log:0007"]
    assert set(report["dropped_citations"]) >= {"log:4242", "made-up"}


def test_fact_without_valid_evidence_is_not_presented_as_a_fact():
    facts = [
        {"statement": "the database ran out of disk", "evidence_ids": ["log:9999"]},
        {"statement": "someone changed the firewall", "evidence_ids": []},
    ]
    report = finalize_report(Report(**{**GOOD, "facts": facts}), {"deploy:2"}, SERVICES)

    assert report["facts"] == []
    assert report["unsupported_statements"] == ["the database ran out of disk", "someone changed the firewall"]
    assert report["status"] == "inconclusive" and report["confidence"] <= 0.4  # no supported facts


def test_conclusive_report_without_facts_is_downgraded():
    report = finalize_report(Report(**{**GOOD, "facts": []}), {"deploy:2", "log:0007"}, SERVICES)

    assert report["status"] == "inconclusive"
    assert any("supported facts" in note for note in report["validation_notes"])


def test_alternative_explanations_are_preserved():
    alternatives = ["traffic spike exceeding pool capacity", "slow queries holding connections"]
    report = finalize_report(Report(**{**GOOD, "alternative_explanations": alternatives}), {"deploy:2"}, SERVICES)

    assert report["alternative_explanations"] == alternatives


@pytest.mark.parametrize(
    "claim",
    [
        "I restarted payments-api to release connections",
        "We have rolled back payments-api to v2.13",
        "IncidentLens deployed a fix",
        "The agent already scaled the pool to 200",
    ],
)
def test_recommendations_claiming_actions_are_removed(claim):
    steps = [claim, "Roll back payments-api to v2.13.x (requires approval)"]
    report = finalize_report(Report(**{**GOOD, "remediation": steps}), {"deploy:2"}, SERVICES)

    assert report["remediation"] == ["Roll back payments-api to v2.13.x (requires approval)"]
    assert any("cannot act" in note for note in report["validation_notes"])


@pytest.mark.parametrize(
    "step",
    [
        "Confirm the deployment has been rolled back before closing the incident",
        "We recommend rolling back payments-api",
        "Restart payments-api after a human approves",
    ],
)
def test_legitimate_recommendations_are_kept(step):
    report = finalize_report(Report(**{**GOOD, "remediation": [step]}), {"deploy:2"}, SERVICES)

    assert report["remediation"] == [step]


def test_explanation_claiming_an_action_is_flagged():
    report = finalize_report(Report(**{**GOOD, "explanation": "I restarted the pool and errors stopped."}),
                             {"deploy:2"}, SERVICES)

    assert any("performs no actions" in note for note in report["validation_notes"])


def test_reports_without_new_fields_still_parse():
    legacy = {k: v for k, v in GOOD.items() if k != "facts"}

    parsed = parse_report(json.dumps(legacy))

    assert parsed.facts == [] and parsed.alternative_explanations == []


def test_fit_for_model_trims_lists_and_marks_truncation():
    payload = {"ok": True, "entries": [{"evidence_id": f"log:{i:04d}", "message": "x" * 200} for i in range(50)]}

    text, fitted = fit_for_model(payload, 2000)

    assert len(text) <= 2000
    assert fitted["truncated_for_size"] is True
    assert 0 < len(fitted["entries"]) < 50
    assert json.loads(text) == fitted


def test_fit_for_model_leaves_small_payloads_alone():
    payload = {"ok": True, "entries": [{"evidence_id": "log:0001"}]}

    text, fitted = fit_for_model(payload, 2000)

    assert fitted == payload and "truncated_for_size" not in fitted
