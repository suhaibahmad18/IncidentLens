"""Resolve cited evidence IDs to the records they name (read-only, scenario-bound)."""

from app.evidence import evidence_kind
from app.tools.context import ToolContext
from app.tools.investigation import GetMetricsArgs, ToolInputError, get_metrics

MAX_LOOKUPS = 30


def resolve_evidence(ctx: ToolContext, evidence_ids: list[str]) -> dict[str, dict | None]:
    """Map each ID to its record, or None if it does not exist in this scenario."""
    ids = list(dict.fromkeys(evidence_ids))[:MAX_LOOKUPS]
    resolved: dict[str, dict | None] = dict.fromkeys(ids)

    log_refs = [i for i in ids if evidence_kind(i) == "log"]
    for ref, ts, service, level, message, attributes in ctx.conn.execute(
        "SELECT l.ref, l.ts, s.name, l.level, l.message, l.attributes FROM logs l"
        " JOIN services s ON s.id = l.service_id WHERE l.scenario_id = %s AND l.ref = ANY(%s)",
        (ctx.scenario_id, log_refs),
    ):
        resolved[ref] = {"kind": "log", "ts": ts.isoformat(), "service": service, "level": level,
                         "message": message, "attributes": attributes}

    deploy_refs = [i for i in ids if evidence_kind(i) == "deploy"]
    for ref, service, version, deployed_at, status, summary in ctx.conn.execute(
        "SELECT d.ref, s.name, d.version, d.deployed_at, d.status, d.change_summary FROM deployments d"
        " JOIN services s ON s.id = d.service_id WHERE d.scenario_id = %s AND d.ref = ANY(%s)",
        (ctx.scenario_id, deploy_refs),
    ):
        resolved[ref] = {"kind": "deploy", "service": service, "version": version,
                         "deployed_at": deployed_at.isoformat(), "status": status, "change_summary": summary}

    for evidence_id in ids:
        kind = evidence_kind(evidence_id)
        if kind == "metric":
            _, service, name = evidence_id.split(":")
            try:
                result = get_metrics(ctx, GetMetricsArgs(service=service, metric=name))
                resolved[evidence_id] = {"kind": "metric", **result.data["series"][0]}
            except (ToolInputError, IndexError):
                pass
        elif kind == "runbook":
            name, _, index = evidence_id.removeprefix("runbook:").partition("#")
            row = ctx.conn.execute(
                "SELECT source, title, content FROM doc_chunks WHERE source = %s AND chunk_index = %s",
                (f"runbooks/{name}", int(index)),
            ).fetchone()
            if row:
                resolved[evidence_id] = {"kind": "runbook", "source": row[0], "title": row[1], "content": row[2]}
    return resolved
