"""Offline: parsing user-supplied evidence, limits, inference and redaction."""

from datetime import UTC, datetime

import pytest

from app.incidents import parsing
from app.incidents.parsing import (
    EvidenceLimitError,
    ParsedEvidence,
    infer_incident_start,
    parse_evidence,
    parse_timestamp,
)

FAKE = "Zx9fakeSecretValue123"

LOGS = """\
2026-07-01T09:58:12Z INFO service=checkout request ok duration_ms=40
2026-07-01 10:00:03,120 ERROR service=checkout payment failed: upstream 503
2026-07-01T10:00:05+0000 WARN [worker] queue depth high
connection reset by peer
2026-07-01T10:00:07Z ERROR service=Payments.API timeout talking to db password=%s
    at db.connect(pool.py:88)
    at handler(api.py:12)
""" % FAKE


def parse(logs="", metrics="", deployments="", service="web"):
    return parse_evidence(logs, metrics, deployments, service)


def test_plain_text_lines_are_parsed():
    result = parse(LOGS)

    assert len(result.logs) == 5
    first, error, warn, bare, last = result.logs
    assert first.ts == datetime(2026, 7, 1, 9, 58, 12, tzinfo=UTC) and first.level == "INFO"
    assert first.service == "checkout"
    assert error.ts == datetime(2026, 7, 1, 10, 0, 3, 120000, tzinfo=UTC) and error.level == "ERROR"
    assert warn.level == "WARN" and warn.service == "web"  # no service=…: default service
    assert bare.ts is None and bare.attributes["timestamp_inferred"] and bare.attributes["level_inferred"]
    assert last.service == "payments-api"  # normalized
    assert "at handler(api.py:12)" in last.message  # indented continuation lines joined


def test_secrets_never_survive_parsing():
    result = parse(LOGS)

    assert result.redactions == 1
    assert all(FAKE not in log.message for log in result.logs)


def test_json_lines_with_secret_attributes():
    line = '{"ts": 1782900007, "severity": "error", "service": "auth", "msg": "login failed", ' \
           '"user": "bob", "session_token": "%s", "nested": {"x": 1}}' % FAKE
    log = parse(line).logs[0]

    assert log.ts == datetime.fromtimestamp(1782900007, UTC)
    assert (log.level, log.service, log.message) == ("ERROR", "auth", "login failed")
    assert log.attributes["user"] == "bob"
    assert log.attributes["session_token"] == "[REDACTED]"
    assert "nested" not in log.attributes  # only scalar attributes are kept


def test_long_lines_are_truncated_and_flagged():
    log = parse("2026-07-01T10:00:00Z ERROR " + "x" * 5000).logs[0]

    assert len(log.message) == parsing.MAX_LINE_CHARS
    assert log.attributes["truncated"] is True


def test_too_many_log_lines_is_rejected(monkeypatch):
    monkeypatch.setattr(parsing, "MAX_LOG_LINES", 3)

    with pytest.raises(EvidenceLimitError, match="logs exceed 3 entries"):
        parse("a\nb\nc\nd")


def test_too_much_log_text_is_rejected(monkeypatch):
    monkeypatch.setattr(parsing, "MAX_LOG_CHARS", 10)

    with pytest.raises(EvidenceLimitError, match="logs exceeds 10 characters"):
        parse("x" * 11)


METRICS = """timestamp,service,metric,value,unit
2026-07-01T09:55:00Z,checkout,error_rate,0.5,percent
2026-07-01T10:00:00Z,checkout,error_rate,12,percent
2026-07-01T10:00:00Z,checkout,error_rate,14,percent
not-a-time,checkout,error_rate,1,percent
2026-07-01T10:01:00Z,checkout,Latency P95 (ms),900,ms
2026-07-01T10:02:00Z,checkout,error_rate,lots,percent
2026-07-01T10:03:00Z,,cpu,55,percent
"""


def test_metrics_csv():
    result = parse(metrics=METRICS)

    names = sorted((m.service, m.name, m.value) for m in result.metrics)
    assert names == [("checkout", "error_rate", 0.5), ("checkout", "error_rate", 14.0),
                     ("checkout", "latency_p95_ms", 900.0), ("web", "cpu", 55.0)]
    assert result.skipped_count == 2
    assert result.skipped == ["metrics line 5: invalid timestamp", "metrics line 7: invalid value"]


def test_metrics_csv_needs_a_header():
    with pytest.raises(EvidenceLimitError, match="needs a header row"):
        parse(metrics="2026-07-01T10:00:00Z,checkout,error_rate,1")


def test_deployments_csv():
    csv_text = """timestamp,service,version,status,description
2026-07-01T09:40:00Z,checkout,v4.2.0,,Enable new retry policy
2026-07-01T09:50:00Z,payments,v1.1.0,rolled back,Revert schema change
2026-07-01T09:52:00Z,payments,,succeeded,missing version
2026-07-01T09:53:00Z,payments,v1.2.0,exploded,bad status
"""
    result = parse(deployments=csv_text)

    assert [(d.service, d.version, d.status) for d in result.deployments] == [
        ("checkout", "v4.2.0", "succeeded"), ("payments", "v1.1.0", "rolled_back")]
    assert [s.split(": ", 1)[1] for s in result.skipped] == [
        "missing version", "status must be one of succeeded, failed, rolled_back"]


def test_skipped_details_never_echo_content():
    result = parse(metrics=f"timestamp,service,metric,value\n2026-07-01T10:00:00Z,web,x,{FAKE}\n")

    assert result.skipped == ["metrics line 2: invalid value"]
    assert FAKE not in " ".join(result.skipped)


def test_too_many_services_is_rejected(monkeypatch):
    monkeypatch.setattr(parsing, "MAX_SERVICES", 2)
    logs = "\n".join(f"2026-07-01T10:00:00Z ERROR service=svc{i} boom" for i in range(3))

    with pytest.raises(EvidenceLimitError, match="more than 2 services"):
        parse(logs)


def test_incident_start_is_the_first_warning_or_error():
    assert infer_incident_start(parse(LOGS)) == datetime(2026, 7, 1, 10, 0, 3, 120000, tzinfo=UTC)
    assert infer_incident_start(parse("2026-07-01T08:00:00Z INFO fine")) == datetime(2026, 7, 1, 8, tzinfo=UTC)
    assert infer_incident_start(ParsedEvidence()) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-01T10:00:00", datetime(2026, 7, 1, 10, tzinfo=UTC)),
        ("2026-07-01T12:00:00+02:00", datetime(2026, 7, 1, 10, tzinfo=UTC)),
        (1782900000000, datetime.fromtimestamp(1782900000, UTC)),
        ("1999-01-01T00:00:00Z", None),
        ("yesterday", None),
        (True, None),
    ],
)
def test_timestamp_parsing(value, expected):
    assert parse_timestamp(value) == expected
