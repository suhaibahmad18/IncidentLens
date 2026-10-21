import json

import pytest

from app.evidence import EVIDENCE_ID_PATTERN
from app.seed_data.ground_truth import GROUND_TRUTH
from app.tools.context import ToolContextError, open_tool_context
from app.tools.investigation import TOOLS, run_tool, tool_definitions
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.db


def ok(result):
    assert result.ok, result.error
    assert all(EVIDENCE_ID_PATTERN.match(e) for e in result.evidence_ids)
    return result.data


# --- query_logs ---


def test_query_logs_groups_repeated_lines_with_first_occurrence_ref(tool_ctx):
    data = ok(run_tool(tool_ctx, "query_logs", {"service": "payments-api", "min_level": "ERROR"}))

    timeout = next(e for e in data["entries"] if "timeout acquiring connection" in e["message"])
    assert timeout["occurrences"] > 10
    assert timeout["evidence_id"].startswith("log:")
    assert timeout["first_seen_minutes_from_incident"] <= 0


def test_query_logs_filters_level_service_and_text(tool_ctx):
    data = ok(run_tool(tool_ctx, "query_logs", {"min_level": "FATAL"}))
    assert {e["level"] for e in data["entries"]} == {"FATAL"}

    data = ok(run_tool(tool_ctx, "query_logs", {"contains": "TOO MANY", "min_level": "INFO"}))
    assert data["entries"] and all("too many" in e["message"].lower() for e in data["entries"])


def test_query_logs_like_wildcards_are_literal(tool_ctx):
    data = ok(run_tool(tool_ctx, "query_logs", {"contains": "%_%", "min_level": "DEBUG"}))

    assert data["entries"] == []


def test_query_logs_respects_limit_and_reports_truncation(tool_ctx):
    data = ok(run_tool(tool_ctx, "query_logs", {"min_level": "INFO", "limit": 3, "minutes_before": 180}))

    assert len(data["entries"]) == 3
    assert data["truncated"] is True


def test_tools_only_see_their_own_scenario(test_db, tool_ctx):
    with open_tool_context(test_db["tools_ro"], "auth-certificate-expiry", FakeEmbedder()) as other:
        theirs = ok(run_tool(other, "query_logs", {"contains": "x509", "min_level": "INFO"}))
    ours = ok(run_tool(tool_ctx, "query_logs", {"contains": "x509", "min_level": "INFO"}))

    assert theirs["entries"]
    assert ours["entries"] == []


# --- get_metrics ---


def test_get_metrics_summarizes_series_with_deviation(tool_ctx):
    data = ok(run_tool(tool_ctx, "get_metrics", {"service": "payments-db", "metric": "db_connections_in_use"}))

    series = data["series"][0]
    assert series["evidence_id"] == "metric:payments-db:db_connections_in_use"
    assert series["baseline_avg"] < 45 and series["max"] > 95
    assert -30 <= series["first_deviation_minutes_from_incident"] <= 0
    assert len(series["points"]) <= 16


def test_get_metrics_unknown_metric_lists_available(tool_ctx):
    result = run_tool(tool_ctx, "get_metrics", {"service": "payments-db", "metric": "nope"})

    assert not result.ok and "db_connections_in_use" in result.error


# --- get_recent_deployments ---


def test_get_recent_deployments_newest_first_with_relative_time(tool_ctx):
    data = ok(run_tool(tool_ctx, "get_recent_deployments", {"hours_before": 6}))

    versions = [d["version"] for d in data["deployments"]]
    assert versions[:2] == ["v3.1.4", "v2.14.0"]
    payments = next(d for d in data["deployments"] if d["service"] == "payments-api")
    assert payments["minutes_from_incident"] == -20.0


def test_deployment_window_is_bounded(tool_ctx):
    data = ok(run_tool(tool_ctx, "get_recent_deployments", {"hours_before": 1}))

    assert "v1.9.2" not in [d["version"] for d in data["deployments"]]  # 150 minutes before


# --- search_runbooks ---


def test_search_runbooks_returns_citable_chunks(tool_ctx):
    data = ok(run_tool(tool_ctx, "search_runbooks", {"query": "connection pool exhausted timeouts", "top_k": 2}))

    assert len(data["results"]) == 2
    assert data["results"][0]["evidence_id"].startswith("runbook:database-connection-pool-exhaustion.md#")


# --- validation and bounds ---


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("query_logs", {"service": "unknown-svc"}, "unknown service"),
        ("query_logs", {"limit": 500}, "limit"),
        ("query_logs", {"minutes_before": 10_000}, "minutes_before"),
        ("query_logs", {"sql": "DROP TABLE logs"}, "Extra inputs are not permitted"),
        ("query_logs", {"contains": "x" * 101}, "contains"),
        ("get_metrics", {}, "service"),
        ("get_metrics", {"service": "payments-db", "metric": "x; DROP TABLE metrics"}, "metric"),
        ("get_recent_deployments", {"hours_before": 72}, "hours_before"),
        ("search_runbooks", {"query": "x"}, "query"),
        ("search_runbooks", {"query": "pool", "top_k": 50}, "top_k"),
        ("run_sql", {"query": "SELECT 1"}, "unknown tool"),
    ],
)
def test_invalid_calls_return_errors_not_exceptions(tool_ctx, name, args, expected):
    result = run_tool(tool_ctx, name, args)

    assert not result.ok
    assert expected in result.error


def test_non_object_arguments_are_rejected(tool_ctx):
    assert not run_tool(tool_ctx, "query_logs", ["service"]).ok


def test_tool_definitions_expose_service_allowlist(tool_ctx):
    definitions = {d["function"]["name"]: d["function"]["parameters"] for d in tool_definitions(tool_ctx)}

    assert set(definitions) == set(TOOLS) == {"query_logs", "get_metrics", "get_recent_deployments", "search_runbooks"}
    assert definitions["query_logs"]["properties"]["service"]["enum"] == list(tool_ctx.services)
    for name, spec in TOOLS.items():
        assert set(definitions[name]["properties"]) == set(spec.args_model.model_fields), name


# --- safety ---


def test_tool_context_refuses_a_non_read_only_role(test_db):
    with pytest.raises(ToolContextError, match="read-only tools_ro"):
        with open_tool_context(test_db["app"], "checkout-db-pool-exhaustion", FakeEmbedder()):
            pass


def test_unknown_scenario_is_rejected(test_db):
    with pytest.raises(ToolContextError, match="unknown scenario"):
        with open_tool_context(test_db["tools_ro"], "no-such-incident", FakeEmbedder()):
            pass


def test_tool_outputs_never_contain_ground_truth(tool_ctx):
    calls = [
        ("query_logs", {"min_level": "INFO", "limit": 50, "minutes_before": 180}),
        ("get_metrics", {"service": "payments-db"}),
        ("get_recent_deployments", {"hours_before": 48}),
        ("search_runbooks", {"query": "root cause expected"}),
    ]
    output = json.dumps([run_tool(tool_ctx, name, args).for_model() for name, args in calls]).lower()

    truth = GROUND_TRUTH["checkout-db-pool-exhaustion"]["root_cause"].lower()
    assert truth not in output
    for marker in ("expected_root_cause", "ground_truth", "holdout", "key_evidence"):
        assert marker not in output
