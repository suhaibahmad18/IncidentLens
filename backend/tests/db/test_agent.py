"""The agent loop end to end against real read-only tools, with a scripted model."""

import json
import re

import pytest

from app.agent.llm import LLMError
from app.agent.loop import AgentLimits, investigate
from app.seed_data.ground_truth import GROUND_TRUTH
from tests.llm_fakes import ListSink, ScriptedLLM, calls, final, tool_call

pytestmark = pytest.mark.db

TITLE, ALERT = "Checkout failures spike", "api-gateway /checkout 5xx rate above 15%"


def seen_ids(messages) -> list[str]:
    tool_text = " ".join(m["content"] for m in messages if m["role"] == "tool")
    return list(dict.fromkeys(re.findall(r'"evidence_id": "([^"]+)"', tool_text)))


def report_citing_seen(messages, **overrides):
    ids = seen_ids(messages)
    return final({
        "status": "conclusive",
        "root_cause": "payments-api v2.14.0 leaks database connections, exhausting the pool",
        "affected_service": "payments-api",
        "confidence": 0.8,
        "explanation": "Pool timeouts start right after the deployment.",
        "evidence": [{"evidence_id": i, "why": "observed"} for i in ids[:4]] + [{"evidence_id": "log:9999", "why": "invented"}],
        "remediation": ["Roll back payments-api (requires human approval)"],
        **overrides,
    })


GATHER = calls(
    tool_call("get_recent_deployments", {"hours_before": 6}),
    tool_call("query_logs", {"service": "payments-api", "min_level": "ERROR"}),
    tool_call("search_runbooks", {"query": "connection pool exhausted"}),
)


def test_completed_investigation_cites_only_real_evidence(tool_ctx):
    llm = ScriptedLLM([GATHER, report_citing_seen])
    sink = ListSink()

    outcome = investigate(tool_ctx, TITLE, ALERT, llm, sink)

    assert outcome.status == "completed"
    report = outcome.report
    assert report["status"] == "conclusive" and report["affected_service"] == "payments-api"
    cited = [c["evidence_id"] for c in report["evidence"]]
    assert cited and set(cited) <= outcome.evidence_seen
    assert "deploy:2" in outcome.evidence_seen
    assert report["dropped_citations"] == ["log:9999"]
    assert [s["type"] for s in sink.steps] == ["llm_call", "tool_call", "tool_call", "retrieval", "llm_call", "final"]
    assert outcome.tool_calls == 3


def test_tool_call_budget_is_enforced(tool_ctx):
    many = calls(*[tool_call("query_logs", {"limit": 1, "contains": f"x{i}"}) for i in range(5)])
    llm = ScriptedLLM([many, many, report_citing_seen])
    limits = AgentLimits(max_iterations=6, max_tool_calls=7)

    outcome = investigate(tool_ctx, TITLE, ALERT, llm, ListSink(), limits)

    assert outcome.tool_calls == 7
    assert llm.requests[-1]["tool_choice"] == "none"  # forced to answer once the budget is used
    assert outcome.status == "completed"
    tool_messages = [m for m in llm.requests[-1]["messages"] if m["role"] == "tool"]
    assert sum("tool budget exhausted" in m["content"] for m in tool_messages) == 3


def test_iteration_limit_forces_a_final_answer(tool_ctx):
    loop_forever = calls(tool_call("get_recent_deployments", {}))
    llm = ScriptedLLM([loop_forever, loop_forever, report_citing_seen])

    outcome = investigate(tool_ctx, TITLE, ALERT, llm, ListSink(), AgentLimits(max_iterations=3))

    assert outcome.status == "completed"
    assert outcome.iterations == 3
    assert [r["tool_choice"] for r in llm.requests] == ["auto", "auto", "none"]


def test_model_that_never_answers_validly_fails_safely(tool_ctx):
    llm = ScriptedLLM([final("I think it's the database."), final("still not json")])

    outcome = investigate(tool_ctx, TITLE, ALERT, llm, ListSink())

    assert outcome.status == "failed"
    assert outcome.error_code == "malformed_response"
    assert outcome.report is None


def test_malformed_answer_is_repaired_once(tool_ctx):
    llm = ScriptedLLM([GATHER, final("not json"), report_citing_seen])

    outcome = investigate(tool_ctx, TITLE, ALERT, llm, ListSink())

    assert outcome.status == "completed"
    assert "could not be accepted" in llm.requests[-1]["messages"][-1]["content"]


def test_conclusion_without_evidence_becomes_inconclusive(tool_ctx):
    llm = ScriptedLLM([final({
        "status": "conclusive", "root_cause": "probably DNS", "affected_service": "api-gateway",
        "confidence": 0.9, "explanation": "gut feeling", "evidence": [{"evidence_id": "log:0001"}],
    })])

    outcome = investigate(tool_ctx, TITLE, ALERT, llm, ListSink())

    assert outcome.status == "completed"
    assert outcome.report["status"] == "inconclusive"
    assert outcome.report["confidence"] <= 0.4
    assert outcome.report["evidence"] == []


@pytest.mark.parametrize("code", ["timeout", "rate_limited", "auth", "connection", "api_error"])
def test_llm_failures_end_the_run_with_a_safe_error(tool_ctx, code):
    sink = ListSink()

    outcome = investigate(tool_ctx, TITLE, ALERT, ScriptedLLM([GATHER, LLMError(code)]), sink)

    assert outcome.status == "failed" and outcome.error_code == code
    assert sink.steps[-1] == {**sink.steps[-1], "type": "llm_call", "error": code}


def test_one_malformed_tool_request_is_tolerated(tool_ctx):
    llm = ScriptedLLM([LLMError("bad_request", 400), GATHER, report_citing_seen])

    outcome = investigate(tool_ctx, TITLE, ALERT, llm, ListSink())

    assert outcome.status == "completed"


def test_bad_tool_arguments_are_returned_to_the_model(tool_ctx):
    bad = calls(tool_call("query_logs", "{not json"), tool_call("get_metrics", {"service": "nope"}),
                tool_call("drop_tables", {}))
    llm = ScriptedLLM([bad, GATHER, report_citing_seen])

    outcome = investigate(tool_ctx, TITLE, ALERT, llm, ListSink())

    errors = [json.loads(m["content"])["error"] for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert errors == ["arguments were not valid JSON", errors[1], errors[2]]
    assert "unknown service" in errors[1] and "unknown tool" in errors[2]
    assert outcome.status == "completed"


def test_tool_results_are_bounded_in_size(tool_ctx):
    big = calls(tool_call("query_logs", {"min_level": "INFO", "limit": 50, "minutes_before": 180}))
    llm = ScriptedLLM([big, report_citing_seen])

    investigate(tool_ctx, TITLE, ALERT, llm, ListSink(), AgentLimits(max_tool_result_chars=1500))

    tool_message = next(m for m in llm.requests[1]["messages"] if m["role"] == "tool")
    assert len(tool_message["content"]) <= 1500
    assert json.loads(tool_message["content"])["truncated_for_size"] is True


def test_prompts_contain_no_ground_truth(tool_ctx):
    llm = ScriptedLLM([GATHER, report_citing_seen])

    investigate(tool_ctx, TITLE, ALERT, llm, ListSink())

    everything_sent = json.dumps(llm.requests).lower()
    assert GROUND_TRUTH["checkout-db-pool-exhaustion"]["root_cause"].lower() not in everything_sent
    for marker in ("expected_root_cause", "ground_truth", "holdout", "key_evidence"):
        assert marker not in everything_sent
