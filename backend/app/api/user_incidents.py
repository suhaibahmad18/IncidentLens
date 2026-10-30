"""Create, extend and delete user-created incidents. Every endpoint requires a logged-in account,
and the owner always comes from the server-side session, never from the request body.

Responses never reveal whether another account's incident exists: anything not owned by the
caller is "incident not found" (404). Demo incidents are public but read-only (403).
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response, status
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import require_user
from app.auth.sessions import AuthUser
from app.db.connection import connect
from app.incidents import store
from app.incidents.parsing import (
    MAX_DEPLOYMENTS_CHARS,
    MAX_LOG_CHARS,
    MAX_METRICS_CHARS,
    EvidenceLimitError,
    infer_incident_start,
    parse_evidence,
)
from app.incidents.redaction import redact

router = APIRouter(prefix="/api")

ServiceName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Slug = Annotated[str, Path(pattern=r"^[a-z0-9-]{1,64}$")]
NOT_FOUND = "incident not found"
DEMO_READ_ONLY = "demo incidents are read-only"


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logs: str = Field("", max_length=MAX_LOG_CHARS)
    metrics_csv: str = Field("", max_length=MAX_METRICS_CHARS)
    deployments_csv: str = Field("", max_length=MAX_DEPLOYMENTS_CHARS)
    default_service: ServiceName = "unknown"


class NewIncidentIn(EvidenceIn):
    title: str = Field(min_length=3, max_length=200)
    alert_summary: str = Field(min_length=3, max_length=2000)
    incident_start: datetime | None = None
    default_service: ServiceName


def _parse(body: EvidenceIn):
    try:
        return parse_evidence(body.logs, body.metrics_csv, body.deployments_csv, body.default_service)
    except EvidenceLimitError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _limits(settings) -> store.EvidenceLimits:
    return store.EvidenceLimits(settings.max_incident_log_entries, settings.max_incident_metric_points,
                                settings.max_incident_deployments)


def _owned(conn, slug: str, user: AuthUser) -> int:
    """The id of `slug` if the caller owns it. Demos -> 403; anything else -> 404."""
    row = conn.execute("SELECT id, kind, owner_id FROM scenarios WHERE slug = %s", (slug,)).fetchone()
    if row is not None and row[1] == "demo":
        raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_READ_ONLY)
    if row is None or row[2] != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return row[0]


@router.post("/incidents", status_code=status.HTTP_201_CREATED)
def create_incident(request: Request, body: NewIncidentIn):
    user = require_user(request)
    settings = request.app.state.settings
    evidence = _parse(body)
    title, title_redactions = redact(body.title.strip())
    alert, alert_redactions = redact(body.alert_summary.strip())
    evidence.redactions += title_redactions + alert_redactions

    start = body.incident_start
    if start is not None:
        start = (start if start.tzinfo else start.replace(tzinfo=UTC)).astimezone(UTC)
    start = start or infer_incident_start(evidence) or datetime.now(UTC)

    if not request.app.state.incident_limiter.allow():
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many incidents created; try again later")
    with connect(settings.database_url) as conn:
        try:
            slug = store.create_incident(conn, title, alert, start, evidence, _limits(settings),
                                         max_user_incidents=settings.max_user_incidents,
                                         owner_id=user.id, max_per_owner=settings.max_incidents_per_user)
        except store.IncidentLimitReached as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
        except store.IncidentFull as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    return {
        "slug": slug,
        "kind": "user",
        "title": title,
        "alert_summary": alert,
        "incident_start": start,
        "evidence": store.evidence_summary(evidence, start),
    }


@router.post("/incidents/{slug}/evidence")
def add_evidence(request: Request, slug: Slug, body: EvidenceIn):
    user = require_user(request)
    settings = request.app.state.settings
    with connect(settings.database_url) as conn:
        _owned(conn, slug, user)
    evidence = _parse(body)
    with connect(settings.database_url) as conn:
        try:
            store.add_evidence(conn, slug, evidence, _limits(settings), owner_id=user.id)
        except store.IncidentNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        except (store.DemoIncidentReadOnly, errors.InsufficientPrivilege):
            raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_READ_ONLY)
        except store.IncidentFull as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        start = conn.execute("SELECT incident_start FROM scenarios WHERE slug = %s", (slug,)).fetchone()[0]
    return {"slug": slug, "evidence": store.evidence_summary(evidence, start)}


@router.delete("/incidents/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_incident(request: Request, slug: Slug):
    """Permanently delete one of the caller's incidents with its evidence, runs and traces."""
    user = require_user(request)
    with connect(request.app.state.settings.database_url) as conn:
        scenario_id = _owned(conn, slug, user)
        try:
            store.delete_incident(conn, scenario_id, owner_id=user.id)
        except store.IncidentNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        except store.IncidentBusy:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "an investigation of this incident is still running; delete it when it finishes")
        except errors.InsufficientPrivilege:
            raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_READ_ONLY)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
