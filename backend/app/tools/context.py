"""Investigation context: a read-only connection bound to exactly one scenario."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

import psycopg
from pydantic import SecretStr

from app.config import TOOLS_RO_ROLE
from app.db.connection import connect
from app.rag.embeddings import Embedder


class ToolContextError(Exception):
    """Safe-to-show setup failure (unknown scenario, wrong database role)."""


@dataclass(frozen=True)
class ToolContext:
    conn: psycopg.Connection
    scenario_id: int
    scenario_slug: str
    incident_start: datetime
    services: tuple[str, ...]
    embedder: Embedder


@contextmanager
def open_tool_context(tools_ro_url: SecretStr, scenario_slug: str, embedder: Embedder) -> Iterator[ToolContext]:
    """The scenario is fixed here by the server; tool arguments can never select another one."""
    with connect(tools_ro_url, autocommit=True) as conn:
        user, read_only = conn.execute(
            "SELECT current_user, current_setting('default_transaction_read_only')"
        ).fetchone()
        if user != TOOLS_RO_ROLE or read_only != "on":
            raise ToolContextError("investigation tools must use the read-only tools_ro role")

        row = conn.execute(
            "SELECT id, incident_start FROM scenarios WHERE slug = %s", (scenario_slug,)
        ).fetchone()
        if row is None:
            raise ToolContextError(f"unknown scenario: {scenario_slug}")
        services = tuple(name for (name,) in conn.execute("SELECT name FROM services ORDER BY name"))
        yield ToolContext(conn, row[0], scenario_slug, row[1], services, embedder)
