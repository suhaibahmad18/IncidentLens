"""Offline regression tests for metric summaries (baseline and first deviation).

The baseline must come only from points well before the incident (> 30 min). It must never
fall back to an in-window point, which may already be the anomaly.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.tools.investigation import GetMetricsArgs, _summarize_series, get_metrics

START = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
CTX = SimpleNamespace(incident_start=START)


def at(minutes: float) -> datetime:
    return START + timedelta(minutes=minutes)


def summarize(points):
    return _summarize_series(CTX, "checkout-svc", "p95_latency_ms", "ms", points)


def test_live_bug_window_without_history_reports_no_baseline():
    """The live run: p95 went 120 -> 2100 ms, but the 15-minute window held only the 2100 point."""
    summary = summarize([(at(0), 2100.0)])

    assert summary["baseline_avg"] is None  # not 2100: the anomaly is never used as a baseline
    assert summary["baseline_available"] is False
    assert summary["first_deviation_minutes_from_incident"] is None
    assert "minutes_before" in summary["baseline_note"]
    assert (summary["min"], summary["max"], summary["latest"]) == (2100.0, 2100.0, 2100.0)


def test_same_data_with_history_detects_the_deviation():
    summary = summarize([(at(-30.5), 120.0), (at(0), 2100.0)])  # the 09:30 point is baseline data

    assert summary["baseline_avg"] == 120.0
    assert summary["baseline_available"] is True
    assert summary["baseline_note"] is None
    assert summary["first_deviation_minutes_from_incident"] == 0.0
    assert (summary["min"], summary["max"]) == (120.0, 2100.0)


def test_first_point_already_anomalous_is_not_the_baseline():
    # All points are inside the last 30 minutes and the first one is already high.
    summary = summarize([(at(-20), 1900.0), (at(-10), 2000.0), (at(0), 2100.0)])

    assert summary["baseline_avg"] is None
    assert summary["first_deviation_minutes_from_incident"] is None
    assert [p["value"] for p in summary["points"]] == [1900.0, 2000.0, 2100.0]  # data is kept


def test_baseline_averages_only_points_before_the_cutoff():
    points = [(at(-60), 100.0), (at(-45), 140.0), (at(-31), 120.0), (at(-20), 125.0), (at(-2), 900.0)]

    summary = summarize(points)

    assert summary["baseline_avg"] == 120.0  # mean of 100, 140, 120 only
    assert summary["first_deviation_minutes_from_incident"] == -2.0  # 125 is within 30%, 900 is not


def test_normal_metric_has_baseline_and_no_deviation():
    summary = summarize([(at(m), 50.0 + (m % 3)) for m in range(-120, 31, 10)])

    assert summary["baseline_available"] is True
    assert 50.0 <= summary["baseline_avg"] <= 53.0
    assert summary["first_deviation_minutes_from_incident"] is None
    assert summary["baseline_note"] is None


def test_point_exactly_at_the_cutoff_is_not_baseline_data():
    summary = summarize([(at(-30), 2100.0), (at(0), 2100.0)])

    assert summary["baseline_avg"] is None


class FakeConn:
    """Answers get_metrics' two queries: available metric names, then the windowed points."""

    def __init__(self, names, rows):
        self.results = [[(n,) for n in names], rows]

    def execute(self, sql, params=None):
        return _Rows(self.results.pop(0))


class _Rows(list):
    def fetchall(self):
        return list(self)


def metrics_ctx(names, rows):
    return SimpleNamespace(incident_start=START, conn=FakeConn(names, rows), scenario_id=1,
                           services=("checkout-svc",))


def test_multiple_series_are_summarized_independently():
    rows = [
        ("error_rate", "percent", at(-60), 0.5), ("error_rate", "percent", at(0), 27.0),
        ("p95_latency_ms", "ms", at(0), 2100.0),
    ]
    result = get_metrics(metrics_ctx(["error_rate", "p95_latency_ms"], rows),
                         GetMetricsArgs(service="checkout-svc", minutes_before=90))

    by_name = {s["metric"]: s for s in result.data["series"]}
    assert by_name["error_rate"]["baseline_avg"] == 0.5
    assert by_name["error_rate"]["first_deviation_minutes_from_incident"] == 0.0
    assert by_name["p95_latency_ms"]["baseline_avg"] is None
    assert by_name["p95_latency_ms"]["first_deviation_minutes_from_incident"] is None
    assert result.evidence_ids == ("metric:checkout-svc:error_rate", "metric:checkout-svc:p95_latency_ms")


def test_no_points_in_window_returns_no_series():
    result = get_metrics(metrics_ctx(["p95_latency_ms"], []), GetMetricsArgs(service="checkout-svc"))

    assert result.ok and result.data["series"] == [] and result.evidence_ids == ()
