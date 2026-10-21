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


def finish_run(conn: psycopg.Connection, run_id: uuid.UUID, report: dict | None, error: str | None) -> None:
    conn.execute(
        "UPDATE runs SET status = %s, final_report = %s, error = %s, finished_at = now() WHERE id = %s",
        ("completed" if error is None else "failed", Jsonb(report) if report is not None else None, error, run_id),
    )


def fail_interrupted_runs(conn: psycopg.Connection) -> int:
    """Runs left pending/running by a restart can never finish: mark them failed."""
    return conn.execute(
        "UPDATE runs SET status = 'failed', error = %s, finished_at = now()"
        " WHERE status IN ('pending', 'running')",
        ("interrupted: the server restarted before the investigation finished",),
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
