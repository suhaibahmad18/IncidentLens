"""Bounded, read-only investigation tools.

Every tool: validated arguments (unknown fields rejected), parameterized SQL, a fixed
scenario from the ToolContext, bounded time windows and result counts, and results that
carry stable evidence IDs for citation. There is no tool that writes or runs free-form SQL.
"""

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.evidence import LEVELS, metric_evidence_id
from app.rag.retrieval import search_runbooks as rag_search
from app.tools.context import ToolContext

MAX_MESSAGE_CHARS = 300
MAX_RUNBOOK_CHARS = 1200
MAX_SERIES_POINTS = 16

Level = Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]


class ToolInputError(Exception):
    """Arguments that passed type validation but are not allowed (e.g. unknown service)."""


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryLogsArgs(StrictArgs):
    service: str | None = None
    min_level: Level = "WARN"
    contains: str | None = Field(None, min_length=2, max_length=100)
    minutes_before: int = Field(60, ge=0, le=180)
    minutes_after: int = Field(30, ge=0, le=60)
    limit: int = Field(20, ge=1, le=50)


class GetMetricsArgs(StrictArgs):
    service: str
    metric: str | None = Field(None, pattern=r"^[a-z0-9_]{1,64}$")
    minutes_before: int = Field(120, ge=0, le=180)
    minutes_after: int = Field(30, ge=0, le=60)


class GetDeploymentsArgs(StrictArgs):
    service: str | None = None
    hours_before: int = Field(6, ge=1, le=48)
    minutes_after: int = Field(30, ge=0, le=60)
    limit: int = Field(10, ge=1, le=20)


class SearchRunbooksArgs(StrictArgs):
    query: str = Field(min_length=3, max_length=300)
    top_k: int = Field(3, ge=1, le=5)


@dataclass(frozen=True)
class ToolResult:
    tool: str
    ok: bool
    data: dict[str, Any]
    evidence_ids: tuple[str, ...] = ()
    error: str | None = None

    def for_model(self) -> dict[str, Any]:
        if not self.ok:
            return {"ok": False, "error": self.error}
        return {"ok": True, **self.data}


def _check_service(ctx: ToolContext, service: str | None) -> None:
    if service is not None and service not in ctx.services:
        raise ToolInputError(f"unknown service '{service[:64]}'; allowed: {', '.join(ctx.services)}")


def _minutes(ctx: ToolContext, ts: datetime) -> float:
    return round((ts - ctx.incident_start).total_seconds() / 60, 1)


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def query_logs(ctx: ToolContext, args: QueryLogsArgs) -> ToolResult:
    _check_service(ctx, args.service)
    params = {
        "scenario": ctx.scenario_id,
        "start": ctx.incident_start - timedelta(minutes=args.minutes_before),
        "end": ctx.incident_start + timedelta(minutes=args.minutes_after),
        "levels": list(LEVELS[LEVELS.index(args.min_level):]),
        "service": args.service,
        "pattern": _like_pattern(args.contains) if args.contains else None,
        "limit": args.limit,
    }
    where = (
        " FROM logs l JOIN services s ON s.id = l.service_id"
        " WHERE l.scenario_id = %(scenario)s AND l.ts BETWEEN %(start)s AND %(end)s"
        " AND l.level = ANY(%(levels)s)"
        " AND (%(service)s::text IS NULL OR s.name = %(service)s)"
        " AND (%(pattern)s::text IS NULL OR l.message ILIKE %(pattern)s ESCAPE '\\')"
    )
    # Identical lines (e.g. retry loops) are grouped so results stay small and readable.
    rows = ctx.conn.execute(
        "SELECT (array_agg(l.ref ORDER BY l.ts, l.ref))[1], s.name, l.level, l.message,"
        " count(*), min(l.ts), max(l.ts), (array_agg(l.attributes ORDER BY l.ts, l.ref))[1]"
        + where
        + " GROUP BY s.name, l.level, l.message ORDER BY min(l.ts), 1 LIMIT %(limit)s",
        params,
    ).fetchall()
    total_groups, total_lines = ctx.conn.execute(
        "SELECT count(DISTINCT (s.name, l.level, l.message)), count(*)" + where, params
    ).fetchone()

    entries = [
        {
            "evidence_id": ref,
            "service": service,
            "level": level,
            "message": message[:MAX_MESSAGE_CHARS],
            "occurrences": count,
            "first_seen": _iso(first),
            "first_seen_minutes_from_incident": _minutes(ctx, first),
            "last_seen_minutes_from_incident": _minutes(ctx, last),
            "attributes": attributes,
        }
        for ref, service, level, message, count, first, last, attributes in rows
    ]
    data = {
        "window": {"minutes_before": args.minutes_before, "minutes_after": args.minutes_after},
        "entries": entries,
        "matched_lines": total_lines,
        "distinct_messages": total_groups,
        "truncated": total_groups > len(entries),
    }
    return ToolResult("query_logs", True, data, tuple(e["evidence_id"] for e in entries))


def get_metrics(ctx: ToolContext, args: GetMetricsArgs) -> ToolResult:
    _check_service(ctx, args.service)
    available = [
        name
        for (name,) in ctx.conn.execute(
            "SELECT DISTINCT m.name FROM metrics m JOIN services s ON s.id = m.service_id"
            " WHERE m.scenario_id = %s AND s.name = %s ORDER BY 1",
            (ctx.scenario_id, args.service),
        )
    ]
    if args.metric is not None and args.metric not in available:
        raise ToolInputError(
            f"no metric '{args.metric}' for {args.service}; available: {', '.join(available) or 'none'}"
        )
    names = [args.metric] if args.metric else available

    rows = ctx.conn.execute(
        "SELECT m.name, m.unit, m.ts, m.value FROM metrics m JOIN services s ON s.id = m.service_id"
        " WHERE m.scenario_id = %s AND s.name = %s AND m.name = ANY(%s)"
        " AND m.ts BETWEEN %s AND %s ORDER BY m.name, m.ts",
        (
            ctx.scenario_id,
            args.service,
            names,
            ctx.incident_start - timedelta(minutes=args.minutes_before),
            ctx.incident_start + timedelta(minutes=args.minutes_after),
        ),
    ).fetchall()

    series: dict[str, dict] = {}
    for name, unit, ts, value in rows:
        series.setdefault(name, {"unit": unit, "points": []})["points"].append((ts, value))

    results = [_summarize_series(ctx, args.service, name, s["unit"], s["points"]) for name, s in series.items()]
    data = {"service": args.service, "series": results}
    return ToolResult("get_metrics", True, data, tuple(r["evidence_id"] for r in results))


def _summarize_series(ctx: ToolContext, service: str, name: str, unit: str, points: list) -> dict:
    values = [value for _, value in points]
    baseline_cutoff = ctx.incident_start - timedelta(minutes=30)
    baseline_values = [value for ts, value in points if ts < baseline_cutoff] or values[:1]
    baseline = statistics.fmean(baseline_values)

    first_deviation = None
    threshold = max(abs(baseline) * 0.3, 1e-9)
    for ts, value in points:
        if ts >= baseline_cutoff and abs(value - baseline) > threshold:
            first_deviation = _minutes(ctx, ts)
            break

    stride = max(1, len(points) // MAX_SERIES_POINTS)
    sampled = points[::stride][:MAX_SERIES_POINTS]
    if points and sampled[-1] is not points[-1]:
        sampled[-1] = points[-1]
    return {
        "evidence_id": metric_evidence_id(service, name),
        "metric": name,
        "unit": unit,
        "baseline_avg": round(baseline, 3),
        "min": min(values),
        "max": max(values),
        "latest": values[-1],
        "first_deviation_minutes_from_incident": first_deviation,
        "points": [{"minutes_from_incident": _minutes(ctx, ts), "value": value} for ts, value in sampled],
    }


def get_recent_deployments(ctx: ToolContext, args: GetDeploymentsArgs) -> ToolResult:
    _check_service(ctx, args.service)
    rows = ctx.conn.execute(
        "SELECT d.ref, s.name, d.version, d.deployed_at, d.status, d.change_summary, d.deployed_by"
        " FROM deployments d JOIN services s ON s.id = d.service_id"
        " WHERE d.scenario_id = %(scenario)s AND d.deployed_at BETWEEN %(start)s AND %(end)s"
        " AND (%(service)s::text IS NULL OR s.name = %(service)s)"
        " ORDER BY d.deployed_at DESC, d.ref LIMIT %(limit)s",
        {
            "scenario": ctx.scenario_id,
            "start": ctx.incident_start - timedelta(hours=args.hours_before),
            "end": ctx.incident_start + timedelta(minutes=args.minutes_after),
            "service": args.service,
            "limit": args.limit,
        },
    ).fetchall()
    deployments = [
        {
            "evidence_id": ref,
            "service": service,
            "version": version,
            "deployed_at": _iso(deployed_at),
            "minutes_from_incident": _minutes(ctx, deployed_at),
            "status": status,
            "change_summary": summary,
            "deployed_by": deployed_by,
        }
        for ref, service, version, deployed_at, status, summary, deployed_by in rows
    ]
    data = {"window": {"hours_before": args.hours_before, "minutes_after": args.minutes_after},
            "deployments": deployments}
    return ToolResult("get_recent_deployments", True, data, tuple(d["evidence_id"] for d in deployments))


def search_runbooks(ctx: ToolContext, args: SearchRunbooksArgs) -> ToolResult:
    hits = rag_search(ctx.conn, ctx.embedder, args.query, args.top_k)
    results = [
        {
            "evidence_id": hit.evidence_id,
            "source": hit.source,
            "title": hit.title,
            "score": hit.score,
            "content": hit.content[:MAX_RUNBOOK_CHARS],
        }
        for hit in hits
    ]
    return ToolResult("search_runbooks", True, {"results": results}, tuple(r["evidence_id"] for r in results))


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[StrictArgs]
    run: Callable[[ToolContext, Any], ToolResult]
    parameters: Callable[[ToolContext], dict]


def _service_param(ctx: ToolContext, description: str) -> dict:
    return {"type": "string", "enum": list(ctx.services), "description": description}


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "query_logs",
            "Search this incident's logs. Identical lines are grouped with an occurrence count. "
            "Times are relative to the incident start.",
            QueryLogsArgs,
            query_logs,
            lambda ctx: {
                "type": "object",
                "properties": {
                    "service": _service_param(ctx, "Only logs from this service (omit for all)."),
                    "min_level": {"type": "string", "enum": list(LEVELS), "default": "WARN"},
                    "contains": {"type": "string", "minLength": 2, "maxLength": 100,
                                 "description": "Case-insensitive substring to match in the message."},
                    "minutes_before": {"type": "integer", "minimum": 0, "maximum": 180, "default": 60},
                    "minutes_after": {"type": "integer", "minimum": 0, "maximum": 60, "default": 30},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            "get_metrics",
            "Summarize a service's metrics around the incident: baseline, min/max, latest, when it "
            "first deviated, and sampled points.",
            GetMetricsArgs,
            get_metrics,
            lambda ctx: {
                "type": "object",
                "properties": {
                    "service": _service_param(ctx, "Service whose metrics to read."),
                    "metric": {"type": "string", "pattern": "^[a-z0-9_]{1,64}$",
                               "description": "One metric name (omit for all of the service's metrics)."},
                    "minutes_before": {"type": "integer", "minimum": 0, "maximum": 180, "default": 120},
                    "minutes_after": {"type": "integer", "minimum": 0, "maximum": 60, "default": 30},
                },
                "required": ["service"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            "get_recent_deployments",
            "List deployments shortly before (and just after) the incident start, newest first.",
            GetDeploymentsArgs,
            get_recent_deployments,
            lambda ctx: {
                "type": "object",
                "properties": {
                    "service": _service_param(ctx, "Only this service (omit for all)."),
                    "hours_before": {"type": "integer", "minimum": 1, "maximum": 48, "default": 6},
                    "minutes_after": {"type": "integer", "minimum": 0, "maximum": 60, "default": 30},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                },
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            "search_runbooks",
            "Semantic search over operational runbooks. Use symptom descriptions as the query.",
            SearchRunbooksArgs,
            search_runbooks,
            lambda ctx: {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 3, "maxLength": 300},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
    )
}


def tool_definitions(ctx: ToolContext) -> list[dict]:
    """OpenAI-compatible function definitions, with the service allowlist as an enum."""
    return [
        {
            "type": "function",
            "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters(ctx)},
        }
        for spec in TOOLS.values()
    ]


def run_tool(ctx: ToolContext, name: str, raw_args: Any) -> ToolResult:
    """Validate and execute one tool call. Bad input becomes an error result, never an exception."""
    spec = TOOLS.get(name)
    if spec is None:
        return ToolResult(str(name)[:64], False, {}, error=f"unknown tool; available: {', '.join(TOOLS)}")
    if not isinstance(raw_args, dict):
        return ToolResult(name, False, {}, error="arguments must be a JSON object")
    try:
        args = spec.args_model.model_validate(raw_args)
        return spec.run(ctx, args)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}" for err in exc.errors()[:5]
        )
        return ToolResult(name, False, {}, error=f"invalid arguments: {problems}")
    except ToolInputError as exc:
        return ToolResult(name, False, {}, error=str(exc))
