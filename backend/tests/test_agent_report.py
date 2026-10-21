"""Offline: report parsing, citation validation and result size bounds."""

import json

import pytest

from app.agent.loop import fit_for_model, finalize_report
from app.agent.report import Report, ReportFormatError, parse_report

SERVICES = ("payments-api", "payments-db")

GOOD = {
    "status": "conclusive",
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
