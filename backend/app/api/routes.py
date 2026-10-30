"""HTTP API. Reads incidents/runs as incidentlens_app; reads telemetry as tools_ro.

Evaluation answers are unreachable here: neither role has any privilege on that table.

Authorization: demo incidents (and their runs) are public and read-only. A user-created incident,
and every run, trace, report and piece of evidence belonging to it, is visible only to its
owner; for anyone else (another account or an anonymous visitor) it behaves exactly like an
incident that does not exist (404), so its existence is not revealed. Ownership always comes from
the server-side session, never from the request. All incident and run lookups go through
_incident_row / _run_row, which enforce this.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Request, status

from app.auth.deps import client_ip, current_user
from app.auth.sessions import AuthUser
from app.config import Settings
from app.db.connection import connect
from app.incidents.store import usable_evidence_count
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


def visible_to(kind: str, owner_id: int | None, user: AuthUser | None) -> bool:
    """Demos are public; a user incident is visible only to its owner."""
    return kind == "demo" or (user is not None and owner_id is not None and owner_id == user.id)


def _incident_row(conn, slug: str, user: AuthUser | None):
    row = conn.execute(
        "SELECT id, slug, title, alert_summary, incident_start, kind, owner_id FROM scenarios WHERE slug = %s",
        (slug,),
    ).fetchone()
    if row is None or not visible_to(row[5], row[6], user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return row


def _incident(row) -> dict:
    _, slug, title, alert_summary, incident_start, kind = row[:6]
    return {"slug": slug, "kind": kind, "title": title, "alert_summary": alert_summary,
            "incident_start": incident_start}


@router.get("/health/ready")
def ready(request: Request):
    with connect(_settings(request).database_url) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "database": "ok", "investigations_enabled": _service(request).available}


@router.get("/config")
def config(request: Request):
    service = _service(request)
    settings = _settings(request)
    # user_incidents_enabled: the feature exists; using it requires a logged-in account.
    return {"investigations_enabled": service.available, "model": service.model_name(),
            "public_demo_mode": settings.public_demo_mode, "user_incidents_enabled": True,
            "registration_enabled": settings.registration_enabled}


@router.get("/incidents")
def list_incidents(request: Request):
    user = current_user(request)
    with connect(_settings(request).database_url) as conn:
        rows = conn.execute(
            "SELECT s.id, s.slug, s.title, s.alert_summary, s.incident_start, s.kind,"
            " (SELECT r.status FROM runs r WHERE r.scenario_id = s.id ORDER BY r.started_at DESC LIMIT 1)"
            " FROM scenarios s WHERE s.kind = 'demo' OR (s.owner_id IS NOT NULL AND s.owner_id = %s)"
            " ORDER BY s.kind DESC, s.incident_start DESC",  # 'user' before 'demo'
            (user.id if user else None,),
        ).fetchall()
    return [{**_incident(row[:6]), "last_run_status": row[6]} for row in rows]


@router.get("/incidents/{slug}")
def incident_detail(request: Request, slug: Slug):
    settings = _settings(request)
    with connect(settings.database_url) as conn:
        row = _incident_row(conn, slug, current_user(request))
        incident = _incident(row)
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM logs WHERE scenario_id = %(s)s),"
            " (SELECT count(*) FROM metrics WHERE scenario_id = %(s)s),"
            " (SELECT count(*) FROM deployments WHERE scenario_id = %(s)s),"
            " greatest((SELECT max(created_at) FROM logs WHERE scenario_id = %(s)s),"
            "          (SELECT max(created_at) FROM metrics WHERE scenario_id = %(s)s),"
            "          (SELECT max(created_at) FROM deployments WHERE scenario_id = %(s)s))",
            {"s": row[0]},
        ).fetchone()
        # Same rule the investigation endpoint enforces, so the UI can explain it up front.
        # updated_at lets the UI flag reports that predate the latest evidence.
        evidence = {"logs": counts[0], "metric_points": counts[1], "deployments": counts[2],
                    "usable": usable_evidence_count(conn, row[0], row[4]), "updated_at": counts[3]}

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
        "evidence": evidence,
        "services": services,
        "logs": logs.data,
        "deployments": deployments.data["deployments"],
        "metrics": metrics,
    }


@router.get("/incidents/{slug}/runs")
def incident_runs(request: Request, slug: Slug):
    with connect(_settings(request).database_url) as conn:
        scenario_id = _incident_row(conn, slug, current_user(request))[0]
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
    user = current_user(request)  # also enforces CSRF for logged-in users
    with connect(_settings(request).database_url) as conn:
        row = _incident_row(conn, slug, user)  # demos: anyone; user incidents: owner only
        if usable_evidence_count(conn, row[0], row[4]) == 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "this incident has no evidence the investigation can use: add logs, metrics or deployments "
                "within 3 hours before to 1 hour after the incident start",
            )
    # Per-account (or per-IP for anonymous demo runs) budget, on top of the global limits.
    if not request.app.state.run_limiter.allow(f"user:{user.id}" if user else f"ip:{client_ip(request)}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "investigation limit reached; try again later")
    try:
        run_id = service.start(row[0])
    except InvestigationsUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "investigations are disabled: no LLM API key configured")
    except TooManyRuns:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many investigations running; try again shortly")
    background.add_task(service.execute, run_id, slug, row[2], row[3])
    return {"run_id": run_id, "status": "pending"}


def _run_row(conn, run_id: uuid.UUID, user: AuthUser | None):
    row = conn.execute(
        "SELECT r.id, s.slug, r.status, r.model, r.started_at, r.finished_at, r.final_report, r.error,"
        " (SELECT count(*) FROM trace_steps t WHERE t.run_id = r.id),"
        " (SELECT count(*) FROM trace_steps t WHERE t.run_id = r.id AND t.step_type IN ('tool_call', 'retrieval')),"
        " s.kind, s.owner_id"
        " FROM runs r JOIN scenarios s ON s.id = r.scenario_id WHERE r.id = %s",
        (run_id,),
    ).fetchone()
    if row is None or not visible_to(row[10], row[11], user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return row


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: uuid.UUID):
    with connect(_settings(request).database_url) as conn:
        r = _run_row(conn, run_id, current_user(request))
    return {"id": r[0], "incident_slug": r[1], "status": r[2], "model": r[3], "started_at": r[4],
            "finished_at": r[5], "report": r[6], "error": r[7], "steps": r[8], "tool_calls": r[9]}


@router.get("/runs/{run_id}/trace")
def get_trace(request: Request, run_id: uuid.UUID):
    with connect(_settings(request).database_url) as conn:
        _run_row(conn, run_id, current_user(request))
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
        r = _run_row(conn, run_id, current_user(request))
    report = r[6] or {}
    # Everything the report references: hypothesis citations and the evidence behind each fact.
    why = {c["evidence_id"]: c.get("why", "") for c in report.get("evidence", [])}
    ids = list(dict.fromkeys(
        [c["evidence_id"] for c in report.get("evidence", [])]
        + [i for fact in report.get("facts", []) for i in fact.get("evidence_ids", [])]
    ))
    if not ids:
        return []
    try:
        with open_tool_context(settings.tools_ro_database_url, r[1], _NoEmbedder()) as ctx:
            records = resolve_evidence(ctx, ids)
    except ToolContextError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return [{"evidence_id": i, "why": why.get(i, ""), "record": records.get(i)} for i in ids]
