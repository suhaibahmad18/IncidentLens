"""Persist user incidents and their evidence (incidentlens_app role, one transaction each).

The role can only INSERT user incidents: it cannot set `kind` (no column privilege) and cannot
UPDATE anything. It may DELETE incidents and runs (the API allows that only for the owner), and
triggers reject any write into, or deletion of, demo incidents.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from psycopg import errors
from psycopg.types.json import Jsonb

from app.incidents.parsing import ParsedEvidence
from app.tools.investigation import MAX_DEPLOYMENT_HOURS_BEFORE, MAX_MINUTES_AFTER, MAX_MINUTES_BEFORE


class IncidentNotFound(Exception):
    pass


class DemoIncidentReadOnly(Exception):
    pass


class IncidentFull(ValueError):
    """Adding this evidence would exceed the per-incident limits."""


class IncidentBusy(Exception):
    """An investigation of this incident is still pending or running."""


class IncidentLimitReached(Exception):
    """The configured maximum number of user incidents (in total, or for this owner) exists already."""


@dataclass(frozen=True)
class EvidenceLimits:
    """Per-incident storage caps (configured through Settings)."""

    log_entries: int = 5000
    metric_points: int = 2000
    deployments: int = 200


def new_slug() -> str:
    # Unique and hard to guess, but NOT an access control: privacy comes from the owner check.
    return f"u-{secrets.token_hex(8)}"


def create_incident(conn: psycopg.Connection, title: str, alert_summary: str, incident_start: datetime,
                    evidence: ParsedEvidence, limits: EvidenceLimits = EvidenceLimits(),
                    max_user_incidents: int | None = None, *, owner_id: int,
                    max_per_owner: int | None = None) -> str:
    """Insert the incident, owned by `owner_id`, and its evidence atomically. Returns the new slug."""
    with conn.transaction():
        if max_user_incidents is not None or max_per_owner is not None:
            # Serialize creations so concurrent requests cannot both pass the cap checks.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('incidentlens:create-incident'))")
            total, owned = conn.execute(
                "SELECT count(*), count(*) FILTER (WHERE owner_id = %s) FROM scenarios WHERE kind = 'user'",
                (owner_id,),
            ).fetchone()
            if max_user_incidents is not None and total >= max_user_incidents:
                raise IncidentLimitReached("the incident storage limit has been reached; try again later")
            if max_per_owner is not None and owned >= max_per_owner:
                raise IncidentLimitReached(
                    f"you can keep at most {max_per_owner} incidents; delete one to create another")
        for _ in range(5):
            slug = new_slug()
            try:
                with conn.transaction():  # savepoint: a slug collision only rolls back this insert
                    scenario_id = conn.execute(
                        "INSERT INTO scenarios (slug, title, alert_summary, incident_start, owner_id)"
                        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (slug, title, alert_summary, incident_start, owner_id),
                    ).fetchone()[0]
                break
            except errors.UniqueViolation:
                continue
        else:
            raise RuntimeError("could not allocate a unique incident slug")
        _insert_evidence(conn, scenario_id, incident_start, evidence, limits)
    return slug


def add_evidence(conn: psycopg.Connection, slug: str, evidence: ParsedEvidence,
                 limits: EvidenceLimits = EvidenceLimits(), *, owner_id: int) -> None:
    with conn.transaction():
        row = conn.execute(
            "SELECT id, kind, incident_start, owner_id FROM scenarios WHERE slug = %s", (slug,)
        ).fetchone()
        if row is None:
            raise IncidentNotFound()
        scenario_id, kind, incident_start, owner = row
        if kind != "user":
            raise DemoIncidentReadOnly()
        if owner != owner_id:
            raise IncidentNotFound()
        # Serialize concurrent additions to one incident so evidence refs stay unique and ordered.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('incident:' || %s))", (slug,))
        _insert_evidence(conn, scenario_id, incident_start, evidence, limits)


def delete_incident(conn: psycopg.Connection, scenario_id: int, *, owner_id: int) -> None:
    """Delete one of `owner_id`'s incidents: its runs (trace steps cascade), then the incident
    (evidence cascades). One transaction, so nothing is left half-deleted."""
    with conn.transaction():
        row = conn.execute("SELECT kind, owner_id FROM scenarios WHERE id = %s", (scenario_id,)).fetchone()
        if row is None or row[0] != "user" or row[1] != owner_id:
            raise IncidentNotFound()
        if conn.execute("SELECT EXISTS (SELECT 1 FROM runs WHERE scenario_id = %s"
                        " AND status IN ('pending', 'running'))", (scenario_id,)).fetchone()[0]:
            raise IncidentBusy()
        try:
            conn.execute("DELETE FROM runs WHERE scenario_id = %s", (scenario_id,))
            conn.execute("DELETE FROM scenarios WHERE id = %s AND owner_id = %s", (scenario_id, owner_id))
        except errors.ForeignKeyViolation:  # a run was started concurrently
            raise IncidentBusy() from None


def _insert_evidence(conn, scenario_id: int, incident_start: datetime, evidence: ParsedEvidence,
                     limits: EvidenceLimits) -> None:
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM logs WHERE scenario_id = %(s)s),"
        " (SELECT count(*) FROM metrics WHERE scenario_id = %(s)s),"
        " (SELECT count(*) FROM deployments WHERE scenario_id = %(s)s)",
        {"s": scenario_id},
    ).fetchone()
    for existing, new, limit, label in (
        (counts[0], len(evidence.logs), limits.log_entries, "log entries"),
        (counts[1], len(evidence.metrics), limits.metric_points, "metric points"),
        (counts[2], len(evidence.deployments), limits.deployments, "deployments"),
    ):
        if existing + new > limit:
            raise IncidentFull(f"an incident can hold at most {limit:,} {label}")

    if not (evidence.logs or evidence.metrics or evidence.deployments):
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO services (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            [(name,) for name in sorted(evidence.services())],
        )
        service_ids = dict(cur.execute(
            "SELECT name, id FROM services WHERE name = ANY(%s)", (sorted(evidence.services()),)
        ).fetchall())

        # Refs continue after existing evidence, in timestamp order (inferred times use the start).
        next_log = _next_number(cur, "logs", scenario_id, "log:")
        ordered = sorted(enumerate(evidence.logs), key=lambda p: (p[1].ts or incident_start, p[0]))
        cur.executemany(
            "INSERT INTO logs (scenario_id, service_id, ref, ts, level, message, attributes)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (scenario_id, service_ids[log.service], f"log:{next_log + i:04d}", log.ts or incident_start,
                 log.level, log.message, Jsonb({**log.attributes, "source": "user"}))
                for i, (_, log) in enumerate(ordered)
            ],
        )
        cur.executemany(
            "INSERT INTO metrics (scenario_id, service_id, name, ts, value, unit) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (scenario_id, service_id, name, ts) DO NOTHING",
            [(scenario_id, service_ids[m.service], m.name, m.ts, m.value, m.unit) for m in evidence.metrics],
        )
        next_deploy = _next_number(cur, "deployments", scenario_id, "deploy:")
        cur.executemany(
            "INSERT INTO deployments (scenario_id, service_id, ref, version, deployed_at, status,"
            " change_summary, deployed_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (scenario_id, service_ids[d.service], f"deploy:{next_deploy + i}", d.version, d.ts, d.status,
                 d.change_summary, d.deployed_by)
                for i, d in enumerate(sorted(evidence.deployments, key=lambda d: (d.ts, d.service)))
            ],
        )


def _next_number(cur, table: str, scenario_id: int, prefix: str) -> int:
    value = cur.execute(
        f"SELECT coalesce(max(substring(ref FROM %s)::int), 0) + 1 FROM {table} WHERE scenario_id = %s",
        (f"^{prefix}([0-9]+)$", scenario_id),
    ).fetchone()[0]
    return value


def usable_evidence_count(conn: psycopg.Connection, scenario_id: int, incident_start: datetime) -> int:
    """Evidence inside the windows the tools can actually see."""
    start = incident_start - timedelta(minutes=MAX_MINUTES_BEFORE)
    end = incident_start + timedelta(minutes=MAX_MINUTES_AFTER)
    deploy_start = incident_start - timedelta(hours=MAX_DEPLOYMENT_HOURS_BEFORE)
    return conn.execute(
        "SELECT (SELECT count(*) FROM logs WHERE scenario_id = %(s)s AND ts BETWEEN %(a)s AND %(b)s)"
        " + (SELECT count(*) FROM metrics WHERE scenario_id = %(s)s AND ts BETWEEN %(a)s AND %(b)s)"
        " + (SELECT count(*) FROM deployments WHERE scenario_id = %(s)s AND deployed_at BETWEEN %(d)s AND %(b)s)",
        {"s": scenario_id, "a": start, "b": end, "d": deploy_start},
    ).fetchone()[0]


def evidence_summary(evidence: ParsedEvidence, incident_start: datetime) -> dict:
    """What was understood, for the API response (counts and reasons only, no content)."""
    start = incident_start - timedelta(minutes=MAX_MINUTES_BEFORE)
    end = incident_start + timedelta(minutes=MAX_MINUTES_AFTER)
    deploy_start = incident_start - timedelta(hours=MAX_DEPLOYMENT_HOURS_BEFORE)
    outside = sum(1 for log in evidence.logs if log.ts and not start <= log.ts <= end)
    outside += sum(1 for m in evidence.metrics if not start <= m.ts <= end)
    outside += sum(1 for d in evidence.deployments if not deploy_start <= d.ts <= end)
    return {
        "logs_parsed": len(evidence.logs),
        "metrics_parsed": len(evidence.metrics),
        "deployments_parsed": len(evidence.deployments),
        "timestamps_inferred": sum(1 for log in evidence.logs if log.ts is None),
        "redactions": evidence.redactions,
        "skipped": evidence.skipped_count,
        "skipped_details": evidence.skipped,
        "outside_investigation_window": outside,
        "services": sorted(evidence.services()),
    }
