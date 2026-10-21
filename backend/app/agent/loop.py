"""The handwritten, bounded investigation loop.

model -> (tool calls -> validated, read-only tools -> bounded results) * N -> JSON report.
Hard limits: iterations, total tool calls, per-result size, wall-clock time, repair attempts.
Every model call and tool call is recorded as a trace step. Nothing here can mutate data:
tools run on the tools_ro connection, and remediation is only ever text.
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import psycopg

from app.agent.llm import ChatClient, LLMError, LLMResponse
from app.agent.prompts import FORCE_FINAL, SYSTEM_PROMPT, incident_brief, repair_request
from app.agent.report import Report, ReportFormatError, parse_report
from app.tools.context import ToolContext
from app.tools.investigation import run_tool, tool_definitions

EVIDENCE_IN_TEXT = re.compile(r'"evidence_id":\s*"([^"]+)"')
INCONCLUSIVE_CONFIDENCE_CAP = 0.4


@dataclass(frozen=True)
class AgentLimits:
    max_iterations: int = 8
    max_tool_calls: int = 10
    max_tool_result_chars: int = 3500
    max_seconds: float = 300.0
    max_repairs: int = 1


class TraceSink(Protocol):
    def record(self, step_type: str, name: str, input: dict, output: Any = None, error: str | None = None,
               latency_ms: int | None = None, input_tokens: int | None = None,
               output_tokens: int | None = None) -> None: ...


@dataclass
class Outcome:
    status: Literal["completed", "failed"]
    report: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    iterations: int = 0
    tool_calls: int = 0
    evidence_seen: set[str] = field(default_factory=set)


def investigate(
    ctx: ToolContext,
    title: str,
    alert_summary: str,
    llm: ChatClient,
    sink: TraceSink,
    limits: AgentLimits = AgentLimits(),
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    started = clock()
    tools = tool_definitions(ctx)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": incident_brief(title, alert_summary, _iso(ctx.incident_start), ctx.services)},
    ]
    outcome = Outcome("failed")
    repairs = 0
    bad_tool_requests = 0

    for iteration in range(1, limits.max_iterations + 1):
        outcome.iterations = iteration
        out_of_budget = (
            outcome.tool_calls >= limits.max_tool_calls
            or iteration == limits.max_iterations
            or clock() - started > limits.max_seconds
        )
        if out_of_budget and messages[-1].get("content") != FORCE_FINAL:
            messages.append({"role": "user", "content": FORCE_FINAL})
        tool_choice = "none" if out_of_budget else "auto"

        try:
            response = _call_model(llm, messages, tools, tool_choice, iteration, sink)
        except LLMError as exc:
            if exc.code == "bad_request" and bad_tool_requests == 0 and not out_of_budget:
                # Most often a malformed tool call the provider refused to parse: nudge once.
                bad_tool_requests += 1
                messages.append({"role": "user", "content": repair_request("a tool call was malformed")})
                continue
            outcome.error_code, outcome.error_message = exc.code, str(exc)
            return outcome

        if response.tool_calls and not out_of_budget:
            _run_tool_calls(ctx, response, messages, outcome, limits, sink)
            continue

        try:
            if response.tool_calls:
                raise ReportFormatError("tools are no longer available; give the final report")
            report = parse_report(response.content)
        except ReportFormatError as exc:
            sink.record("final", "invalid_report", {"iteration": iteration}, error=str(exc))
            if repairs >= limits.max_repairs or iteration == limits.max_iterations:
                outcome.error_code = "malformed_response"
                outcome.error_message = "The model did not return a valid report."
                return outcome
            repairs += 1
            messages.append({"role": "assistant", "content": (response.content or "")[:4000]})
            messages.append({"role": "user", "content": repair_request(str(exc))})
            continue

        outcome.status = "completed"
        outcome.report = finalize_report(report, outcome.evidence_seen, ctx.services)
        sink.record("final", "report", {"iteration": iteration}, output=outcome.report)
        return outcome

    outcome.error_code = "budget_exhausted"
    outcome.error_message = "The investigation ran out of steps before producing a report."
    return outcome


def _call_model(llm, messages, tools, tool_choice, iteration, sink) -> LLMResponse:
    started = time.monotonic()
    step_input = {"iteration": iteration, "messages": len(messages), "tool_choice": tool_choice}
    try:
        response = llm.complete(messages, tools, tool_choice)
    except LLMError as exc:
        sink.record("llm_call", llm.model, step_input, error=exc.code, latency_ms=_ms(started))
        raise
    sink.record(
        "llm_call",
        llm.model,
        step_input,
        output={
            "content": (response.content or "")[:2000],
            "tool_calls": [{"name": c.name, "arguments": c.arguments[:500]} for c in response.tool_calls],
            "finish_reason": response.finish_reason,
            "cached": response.cached,
        },
        latency_ms=_ms(started),
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
    return response


def _run_tool_calls(ctx, response: LLMResponse, messages, outcome: Outcome, limits: AgentLimits, sink) -> None:
    messages.append({
        "role": "assistant",
        "content": response.content or "",
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in response.tool_calls
        ],
    })
    for call in response.tool_calls:
        started = time.monotonic()
        arguments: Any = None
        if outcome.tool_calls >= limits.max_tool_calls:
            payload = {"ok": False, "error": "tool budget exhausted; give the final report"}
        else:
            outcome.tool_calls += 1
            try:
                arguments = json.loads(call.arguments) if call.arguments.strip() else {}
                payload = run_tool(ctx, call.name, arguments).for_model()
            except json.JSONDecodeError:
                payload = {"ok": False, "error": "arguments were not valid JSON"}
            except psycopg.errors.QueryCanceled:
                payload = {"ok": False, "error": "the query took too long; narrow the time window"}
            except psycopg.Error:
                payload = {"ok": False, "error": "the tool failed to read data"}

        text, payload = fit_for_model(payload, limits.max_tool_result_chars)
        outcome.evidence_seen.update(EVIDENCE_IN_TEXT.findall(text))  # only what the model saw
        messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
        sink.record(
            "retrieval" if call.name == "search_runbooks" else "tool_call",
            call.name[:64],
            {"arguments": arguments if isinstance(arguments, dict) else call.arguments[:500]},
            output=payload,
            error=None if payload.get("ok") else payload.get("error"),
            latency_ms=_ms(started),
        )


def fit_for_model(payload: dict, limit: int) -> tuple[str, dict]:
    """Shrink the longest lists until the JSON fits; mark the result as truncated."""
    payload = json.loads(json.dumps(payload, default=str))
    text = json.dumps(payload)
    while len(text) > limit:
        longest = _longest_list(payload)
        if longest is None or not longest:
            payload = {"ok": payload.get("ok", False), "error": "result too large; narrow the query"}
            return json.dumps(payload), payload
        longest.pop()
        payload["truncated_for_size"] = True
        text = json.dumps(payload)
    return text, payload


def _longest_list(node: Any) -> list | None:
    best, best_size = None, 0
    stack = [node]
    while stack:
        current = stack.pop()
        children = current.values() if isinstance(current, dict) else current if isinstance(current, list) else ()
        if isinstance(current, list) and current:
            size = len(json.dumps(current))
            if size > best_size:
                best, best_size = current, size
        stack.extend(child for child in children if isinstance(child, (dict, list)))
    return best


def finalize_report(report: Report, evidence_seen: set[str], services: tuple[str, ...]) -> dict:
    """Keep only citations the tools actually returned; downgrade unsupported conclusions."""
    notes: list[str] = []
    citations = [c for c in report.evidence if c.evidence_id in evidence_seen]
    dropped = [c.evidence_id for c in report.evidence if c.evidence_id not in evidence_seen]
    if dropped:
        notes.append("Removed citations that no tool returned in this investigation.")

    affected = report.affected_service if report.affected_service in services else None
    if report.affected_service and affected is None:
        notes.append("Removed an affected service that is not a known service.")

    status, confidence = report.status, report.confidence
    if status == "conclusive" and (not citations or affected is None):
        status = "inconclusive"
        notes.append("Downgraded to inconclusive: the conclusion lacked valid evidence or a known service.")
    if status == "inconclusive":
        confidence = min(confidence, INCONCLUSIVE_CONFIDENCE_CAP)

    return {
        "status": status,
        "root_cause": report.root_cause,
        "affected_service": affected,
        "confidence": round(confidence, 2),
        "explanation": report.explanation,
        "evidence": [c.model_dump() for c in citations],
        "remediation": report.remediation,
        "missing_information": report.missing_information,
        "advisory_only": True,
        "dropped_citations": dropped,
        "validation_notes": notes,
    }


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _iso(ts) -> str:
    return ts.isoformat().replace("+00:00", "Z")
