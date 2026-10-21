"""Load the simulated incidents (and their hidden ground truth) into the database.

Run from backend/:  uv run python -m app.db.seed

Runs as admin because application roles cannot write input data. Repeatable: each run
upserts services and scenarios and replaces each scenario's telemetry in one transaction,
so nothing is duplicated and evidence IDs (log:0001, deploy:1, ...) stay stable.
"""

import sys

import psycopg
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from app.config import get_admin_settings
from app.db.connection import connect
from app.seed_data.builder import SERVICES, ScenarioData
from app.seed_data.ground_truth import GROUND_TRUTH
from app.seed_data.scenarios import ALL_SCENARIOS


def build_all() -> list[ScenarioData]:
    scenarios = [factory() for factory in ALL_SCENARIOS]
    slugs = {s.slug for s in scenarios}
    if slugs != set(GROUND_TRUTH):
        raise ValueError("every scenario needs exactly one ground-truth entry")
    return scenarios


def seed(url: SecretStr) -> dict[str, dict[str, int]]:
    counts = {}
    with connect(url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO services (name, description) VALUES (%s, %s)"
                " ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description",
                list(SERVICES.items()),
            )
            service_ids = dict(cur.execute("SELECT name, id FROM services").fetchall())

            for data in build_all():
                counts[data.slug] = _seed_scenario(cur, data, service_ids)
    return counts


def _seed_scenario(cur: psycopg.Cursor, data: ScenarioData, service_ids: dict) -> dict[str, int]:
    scenario_id = cur.execute(
        "INSERT INTO scenarios (slug, title, alert_summary, incident_start)"
        " VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (slug) DO UPDATE SET title = EXCLUDED.title,"
        " alert_summary = EXCLUDED.alert_summary, incident_start = EXCLUDED.incident_start"
        " RETURNING id",
        (data.slug, data.title, data.alert_summary, data.incident_start),
    ).fetchone()[0]

    for table in ("logs", "metrics", "deployments"):
        cur.execute(f"DELETE FROM {table} WHERE scenario_id = %s", (scenario_id,))

    logs = data.log_refs()
    cur.executemany(
        "INSERT INTO logs (scenario_id, service_id, ref, ts, level, message, attributes)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [
            (scenario_id, service_ids[log.service], ref, log.ts, log.level, log.message, Jsonb(log.attributes))
            for ref, log in logs
        ],
    )
    deployments = data.deployment_refs()
    cur.executemany(
        "INSERT INTO deployments (scenario_id, service_id, ref, version, deployed_at, status,"
        " change_summary, deployed_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (scenario_id, service_ids[d.service], ref, d.version, d.deployed_at, d.status,
             d.change_summary, d.deployed_by)
            for ref, d in deployments
        ],
    )
    points = [
        (scenario_id, service_ids[m.service], m.name, ts, value, m.unit)
        for m in data.metrics
        for ts, value in m.points
    ]
    cur.executemany(
        "INSERT INTO metrics (scenario_id, service_id, name, ts, value, unit)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        points,
    )

    truth = GROUND_TRUTH[data.slug]
    cur.execute(
        "INSERT INTO scenario_ground_truth (scenario_id, split, expected_root_cause,"
        " root_cause_service_id, root_cause_keywords, key_evidence_ids)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (scenario_id) DO UPDATE SET split = EXCLUDED.split,"
        " expected_root_cause = EXCLUDED.expected_root_cause,"
        " root_cause_service_id = EXCLUDED.root_cause_service_id,"
        " root_cause_keywords = EXCLUDED.root_cause_keywords,"
        " key_evidence_ids = EXCLUDED.key_evidence_ids",
        (scenario_id, truth["split"], truth["root_cause"], service_ids[truth["service"]],
         Jsonb(truth["keywords"]), data.key_evidence_ids()),
    )
    return {"logs": len(logs), "metric_points": len(points), "deployments": len(deployments)}


def main() -> int:
    step = "loading settings"
    try:
        url = get_admin_settings().database_admin_url
        step = "seeding scenarios"
        counts = seed(url)
    except psycopg.errors.DatabaseError as exc:
        detail = exc.diag.message_primary if exc.diag else None
        print(f"seed failed while {step}: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"seed failed while {step}: {type(exc).__name__}", file=sys.stderr)
        return 1

    for slug, c in counts.items():
        print(f"{slug}: {c['logs']} logs, {c['metric_points']} metric points, {c['deployments']} deployments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
