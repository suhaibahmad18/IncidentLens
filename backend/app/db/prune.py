"""Retention: delete old USER-created incidents, stale LLM-cache entries and expired login
sessions. Never touches demos or accounts.

Run from backend/ (admin connection, like the other maintenance commands):
    python -m app.db.prune                      # dry run: shows what WOULD be deleted
    python -m app.db.prune --execute            # actually delete
    python -m app.db.prune --older-than-days 7  # override USER_INCIDENT_RETENTION_DAYS

Deleting an incident removes its runs (and their trace steps) and its evidence (logs, metrics,
deployments) through foreign keys. Services no longer referenced by any incident are removed
(the seed recreates demo services). Everything happens in one transaction. This command never
runs automatically; schedule it explicitly if you want periodic retention.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

import psycopg
from pydantic import SecretStr

from app.config import get_admin_settings, get_settings
from app.db.connection import connect

UNREFERENCED_SERVICES = (
    "SELECT s.id FROM services s WHERE"
    " NOT EXISTS (SELECT 1 FROM logs WHERE service_id = s.id)"
    " AND NOT EXISTS (SELECT 1 FROM metrics WHERE service_id = s.id)"
    " AND NOT EXISTS (SELECT 1 FROM deployments WHERE service_id = s.id)"
    " AND NOT EXISTS (SELECT 1 FROM scenario_ground_truth WHERE root_cause_service_id = s.id)"
)


def prune(url: SecretStr, older_than_days: int, execute: bool, now: datetime | None = None) -> dict:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
    with connect(url) as conn:
        database = conn.execute("SELECT current_database()").fetchone()[0]
        targets = conn.execute(
            "SELECT id, slug FROM scenarios WHERE kind = 'user' AND created_at < %s ORDER BY created_at",
            (cutoff,),
        ).fetchall()
        ids = [row[0] for row in targets]
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM runs WHERE scenario_id = ANY(%(ids)s)),"
            " (SELECT count(*) FROM trace_steps t JOIN runs r ON r.id = t.run_id WHERE r.scenario_id = ANY(%(ids)s)),"
            " (SELECT count(*) FROM logs WHERE scenario_id = ANY(%(ids)s)),"
            " (SELECT count(*) FROM metrics WHERE scenario_id = ANY(%(ids)s)),"
            " (SELECT count(*) FROM deployments WHERE scenario_id = ANY(%(ids)s)),"
            " (SELECT count(*) FROM llm_cache WHERE created_at < %(cutoff)s),"
            " (SELECT count(*) FROM sessions WHERE expires_at <= now())",
            {"ids": ids, "cutoff": cutoff},
        ).fetchone()
        summary = {
            "database": database,
            "cutoff": cutoff.isoformat(),
            "executed": execute,
            "user_incidents": len(ids),
            "slugs": [row[1] for row in targets],
            "runs": counts[0], "trace_steps": counts[1],
            "logs": counts[2], "metric_points": counts[3], "deployments": counts[4],
            "llm_cache_entries": counts[5],
            "expired_sessions": counts[6],
        }
        if execute:
            conn.execute("DELETE FROM runs WHERE scenario_id = ANY(%s)", (ids,))  # trace_steps cascade
            deleted = conn.execute(
                "DELETE FROM scenarios WHERE id = ANY(%s) AND kind = 'user'", (ids,)  # evidence cascades
            ).rowcount
            if deleted != len(ids):
                raise RuntimeError("unexpected number of incidents deleted; transaction rolled back")
            conn.execute("DELETE FROM llm_cache WHERE created_at < %s", (cutoff,))
            conn.execute("DELETE FROM sessions WHERE expires_at <= now()")
            summary["services_removed"] = conn.execute(
                f"DELETE FROM services WHERE id IN ({UNREFERENCED_SERVICES})"
            ).rowcount
        else:
            summary["services_removed"] = 0
            conn.rollback()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete old user-created incidents (never demos).")
    parser.add_argument("--older-than-days", type=int, default=None,
                        help="default: USER_INCIDENT_RETENTION_DAYS (30)")
    parser.add_argument("--execute", action="store_true", help="actually delete (default is a dry run)")
    args = parser.parse_args()

    try:
        days = args.older_than_days or get_settings().user_incident_retention_days
        if days < 1:
            print("--older-than-days must be at least 1", file=sys.stderr)
            return 2
        summary = prune(get_admin_settings().database_admin_url, days, args.execute)
    except psycopg.errors.DatabaseError as exc:
        detail = exc.diag.message_primary if exc.diag else None
        print(f"prune failed: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"prune failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    mode = "DELETED" if summary["executed"] else "DRY RUN (nothing deleted; add --execute to delete)"
    print(f"{mode} in database '{summary['database']}': user incidents created before {summary['cutoff']}")
    for key in ("user_incidents", "runs", "trace_steps", "logs", "metric_points", "deployments",
                "llm_cache_entries", "expired_sessions", "services_removed"):
        print(f"  {key}: {summary[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
