"""Runs and trace steps, written with the incidentlens_app role (append-only traces)."""

import json
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

MAX_TRACE_JSON_CHARS = 20_000


def create_run(conn: psycopg.Connection, scenario_id: int, model: str) -> uuid.UUID:
    return conn.execute(
        "INSERT INTO runs (scenario_id, model) VALUES (%s, %s) RETURNING id", (scenario_id, model)
    ).fetchone()[0]


def mark_running(conn: psycopg.Connection, run_id: uuid.UUID) -> None:
    conn.execute("UPDATE runs SET status = 'running' WHERE id = %s AND status = 'pending'", (run_id,))


def finish_run(conn: psycopg.Connection, run_id: uuid.UUID, report: dict | None, error: str | None) -> bool:
    """Store the outcome, unless the run is already finished.

    A run can be finished elsewhere first: another app instance may have marked it failed as
    interrupted. A finished run is never overwritten. The status check and the write are a single
    UPDATE, so concurrent writers cannot both succeed. Returns True if this call finished the run.
    """
    return conn.execute(
        "UPDATE runs SET status = %s, final_report = %s, error = %s, finished_at = now()"
        " WHERE id = %s AND status IN ('pending', 'running')",
        ("completed" if error is None else "failed", Jsonb(report) if report is not None else None, error, run_id),
    ).rowcount == 1


INTERRUPTED_ERROR = "interrupted: the investigation stopped making progress (for example, the server restarted)"


def fail_interrupted_runs(conn: psycopg.Connection, stale_after_seconds: float) -> int:
    """Mark failed the pending/running runs that cannot still be alive.

    A run counts as interrupted only when it has recorded no progress (no new trace step, or no
    start, for runs without steps) for `stale_after_seconds`. Live runs on any instance record a
    trace step after every model and tool call, so a run that is merely slow, or that is running
    on another instance during an overlapping deploy, is left alone. Timestamps all come from the
    database clock. One atomic UPDATE: a run finished concurrently is skipped, never overwritten.
    """
    return conn.execute(
        "UPDATE runs r SET status = 'failed', error = %s, finished_at = now()"
        " WHERE r.status IN ('pending', 'running')"
        " AND greatest(r.started_at, (SELECT max(t.created_at) FROM trace_steps t WHERE t.run_id = r.id))"
        "     < now() - make_interval(secs => %s)",
        (INTERRUPTED_ERROR, stale_after_seconds),
    ).rowcount


def _bounded(value: Any) -> Any:
    if value is None:
        return None
    text = json.dumps(value, default=str)
    if len(text) <= MAX_TRACE_JSON_CHARS:
        return json.loads(text)
    return {"truncated": True, "preview": text[:MAX_TRACE_JSON_CHARS]}


class DbTraceSink:
    """Each step is committed immediately (autocommit) so the UI can show progress live."""

    def __init__(self, conn: psycopg.Connection, run_id: uuid.UUID):
        self._conn = conn
        self._run_id = run_id
        self._next_index = 0

    def record(self, step_type, name, input, output=None, error=None, latency_ms=None,
               input_tokens=None, output_tokens=None) -> None:
        self._conn.execute(
            "INSERT INTO trace_steps (run_id, step_index, step_type, name, input, output, error,"
            " latency_ms, input_tokens, output_tokens) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                self._run_id, self._next_index, step_type, name[:64], Jsonb(_bounded(input) or {}),
                Jsonb(_bounded(output)) if output is not None else None,
                error[:500] if error else None, latency_ms, input_tokens, output_tokens,
            ),
        )
        self._next_index += 1
