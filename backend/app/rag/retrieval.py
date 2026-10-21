"""Semantic search over runbook chunks (cosine distance, exact scan: the corpus is small)."""

from dataclasses import dataclass

import psycopg

from app.evidence import runbook_evidence_id
from app.rag.embeddings import Embedder, vector_literal


@dataclass(frozen=True)
class RunbookHit:
    evidence_id: str
    source: str
    title: str
    content: str
    score: float


def search_runbooks(conn: psycopg.Connection, embedder: Embedder, query: str, top_k: int) -> list[RunbookHit]:
    query_vector = vector_literal(embedder.embed([query])[0])
    rows = conn.execute(
        "SELECT source, chunk_index, title, content, 1 - (embedding <=> %(q)s::vector) AS score"
        " FROM doc_chunks WHERE embedding IS NOT NULL"
        " ORDER BY embedding <=> %(q)s::vector, source, chunk_index LIMIT %(k)s",
        {"q": query_vector, "k": top_k},
    ).fetchall()
    return [
        RunbookHit(runbook_evidence_id(source, index), source, title, content, round(float(score), 4))
        for source, index, title, content, score in rows
    ]
