"""HTTP API. Reads incidents/runs as incidentlens_app; reads telemetry as tools_ro.

Evaluation answers are unreachable here: neither role has any privilege on that table.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Request, status

from app.config import Settings
from app.db.connection import connect
from app.runs.service import InvestigationService, InvestigationsUnavailable, TooManyRuns
from app.tools.context import ToolContextError, open_tool_context
from app.tools.evidence_lookup import resolve_evidence
from app.tools.investigation import (
    GetDeploymentsArgs,
    GetMetricsArgs,
    QueryLogsArgs,
    get_metrics,
    get_recent_deployments,
    query_logs,
)

router = APIRouter(prefix="/api")

Slug = Annotated[str, Path(pattern=r"^[a-z0-9-]{1,64}$")]


class _NoEmbedder:
    """Read endpoints never run semantic search."""

    model_name = "none"

    def embed(self, texts):
        raise RuntimeError("embedding is not available on read endpoints")


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _service(request: Request) -> InvestigationService:
    return request.app.state.investigations


def _incident_row(conn, slug: str):
    row = conn.execute(
        "SELECT id, slug, title, alert_summary, incident_start FROM scenarios WHERE slug = %s", (slug,)
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return row


def _incident(row) -> dict:
    _, slug, title, alert_summary, incident_start = row
    return {"slug": slug, "title": title, "alert_summary": alert_summary, "incident_start": incident_start}


@router.get("/health/ready")
def ready(request: Request):
    with connect(_settings(request).database_url) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "database": "ok", "investigations_enabled": _service(request).available}


@router.get("/config")
def config(request: Request):
    service = _service(request)
    return {"investigations_enabled": service.available, "model": service.model_name()}


@router.get("/incidents")
def list_incidents(request: Request):
    with connect(_settings(request).database_url) as conn:
        rows = conn.execute(
            "SELECT s.id, s.slug, s.title, s.alert_summary, s.incident_start,"
            " (SELECT r.status FROM runs r WHERE r.scenario_id = s.id ORDER BY r.started_at DESC LIMIT 1)"
            " FROM scenarios s ORDER BY s.incident_start DESC"
        ).fetchall()
    return [{**_incident(row[:5]), "last_run_status": row[5]} for row in rows]


@router.get("/incidents/{slug}")
def incident_detail(request: Request, slug: Slug):
    settings = _settings(request)
    with connect(settings.database_url) as conn:
        incident = _incident(_incident_row(conn, slug))

    # The same read-only views the agent's tools provide, for the engineer to inspect.
    with open_tool_context(settings.tools_ro_database_url, slug, _NoEmbedder()) as ctx:
        logs = query_logs(ctx, QueryLogsArgs(min_level="WARN", minutes_before=120, minutes_after=60, limit=25))
        deployments = get_recent_deployments(ctx, GetDeploymentsArgs(hours_before=6, minutes_after=60, limit=20))
        services_with_metrics = [
            name for (name,) in ctx.conn.execute(
                "SELECT DISTINCT s.name FROM metrics m JOIN services s ON s.id = m.service_id"
                " WHERE m.scenario_id = %s ORDER BY 1", (ctx.scenario_id,))
        ]
        metrics = [
            series
            for service in services_with_metrics
            for series in get_metrics(ctx, GetMetricsArgs(service=service, minutes_after=60)).data["series"]
        ]
        services = list(ctx.services)

    return {
        **incident,
        "services": services,
        "logs": logs.data,
        "deployments": deployments.data["deployments"],
        "metrics": metrics,
    }


@router.get("/incidents/{slug}/runs")
def incident_runs(request: Request, slug: Slug):
    with connect(_settings(request).database_url) as conn:
        scenario_id = _incident_row(conn, slug)[0]
        rows = conn.execute(
            "SELECT id, status, model, started_at, finished_at, final_report->>'status',"
            " (final_report->>'confidence')::float FROM runs WHERE scenario_id = %s"
            " ORDER BY started_at DESC LIMIT 10",
            (scenario_id,),
        ).fetchall()
    return [
        {"id": r[0], "status": r[1], "model": r[2], "started_at": r[3], "finished_at": r[4],
         "report_status": r[5], "confidence": r[6]}
        for r in rows
    ]


@router.post("/incidents/{slug}/investigations", status_code=status.HTTP_202_ACCEPTED)
def start_investigation(request: Request, slug: Slug, background: BackgroundTasks):
    service = _service(request)
    with connect(_settings(request).database_url) as conn:
        row = _incident_row(conn, slug)
    try:
        run_id = service.start(row[0])
    except InvestigationsUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "investigations are disabled: no LLM API key configured")
    except TooManyRuns:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many investigations running; try again shortly")
    background.add_task(service.execute, run_id, slug, row[2], row[3])
    return {"run_id": run_id, "status": "pending"}


def _run_row(conn, run_id: uuid.UUID):
    row = conn.execute(
        "SELECT r.id, s.slug, r.status, r.model, r.started_at, r.finished_at, r.final_report, r.error,"
        " (SELECT count(*) FROM trace_steps t WHERE t.run_id = r.id),"
        " (SELECT count(*) FROM trace_steps t WHERE t.run_id = r.id AND t.step_type IN ('tool_call', 'retrieval'))"
        " FROM runs r JOIN scenarios s ON s.id = r.scenario_id WHERE r.id = %s",
        (run_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return row


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: uuid.UUID):
    with connect(_settings(request).database_url) as conn:
        r = _run_row(conn, run_id)
    return {"id": r[0], "incident_slug": r[1], "status": r[2], "model": r[3], "started_at": r[4],
            "finished_at": r[5], "report": r[6], "error": r[7], "steps": r[8], "tool_calls": r[9]}


@router.get("/runs/{run_id}/trace")
def get_trace(request: Request, run_id: uuid.UUID):
    with connect(_settings(request).database_url) as conn:
        _run_row(conn, run_id)
        rows = conn.execute(
            "SELECT step_index, step_type, name, input, output, error, latency_ms, input_tokens,"
            " output_tokens, created_at FROM trace_steps WHERE run_id = %s ORDER BY step_index",
            (run_id,),
        ).fetchall()
    keys = ("index", "type", "name", "input", "output", "error", "latency_ms", "input_tokens",
            "output_tokens", "created_at")
    return [dict(zip(keys, row, strict=True)) for row in rows]


@router.get("/runs/{run_id}/evidence")
def get_evidence(request: Request, run_id: uuid.UUID):
    settings = _settings(request)
    with connect(settings.database_url) as conn:
        r = _run_row(conn, run_id)
    report = r[6] or {}
    citations = report.get("evidence", [])
    if not citations:
        return []
    try:
        with open_tool_context(settings.tools_ro_database_url, r[1], _NoEmbedder()) as ctx:
            records = resolve_evidence(ctx, [c["evidence_id"] for c in citations])
    except ToolContextError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return [
        {"evidence_id": c["evidence_id"], "why": c.get("why", ""), "record": records.get(c["evidence_id"])}
        for c in citations
    ]
