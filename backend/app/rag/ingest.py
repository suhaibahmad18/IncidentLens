"""Chunk, embed and store the runbooks in doc_chunks.

Run from backend/:  uv run python -m app.rag.ingest

Runs as admin (application roles cannot write doc_chunks). Repeatable: a runbook is
re-embedded only when its content hash or the embedding model changed; runbooks deleted
from disk are removed from the database.
"""

import hashlib
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from app.config import get_admin_settings
from app.db.connection import connect
from app.rag.chunking import chunk_markdown
from app.rag.embeddings import Embedder, FastEmbedEmbedder, vector_literal

RUNBOOKS_DIR = Path(__file__).resolve().parents[2] / "runbooks"
SOURCE_PREFIX = "runbooks/"


def ingest_runbooks(
    url: SecretStr,
    embedder: Embedder,
    directory: Path = RUNBOOKS_DIR,
    source_prefix: str = SOURCE_PREFIX,
) -> dict[str, list[str]]:
    result = {"added": [], "updated": [], "unchanged": [], "removed": []}
    files = {f"{source_prefix}{path.name}": path for path in sorted(directory.glob("*.md"))}

    with connect(url) as conn:
        stored = {
            source: (sha, model)
            for source, sha, model in conn.execute(
                "SELECT source, min(metadata->>'sha256'), min(metadata->>'model')"
                " FROM doc_chunks WHERE starts_with(source, %s) GROUP BY source",
                (source_prefix,),
            )
        }

        for source, path in files.items():
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if stored.get(source) == (sha, embedder.model_name):
                result["unchanged"].append(source)
                continue

            chunks = chunk_markdown(source, text)
            vectors = embedder.embed([chunk.content for chunk in chunks])
            conn.execute("DELETE FROM doc_chunks WHERE source = %s", (source,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO doc_chunks (source, chunk_index, title, content, embedding, metadata)"
                    " VALUES (%s, %s, %s, %s, %s::vector, %s)",
                    [
                        (c.source, c.chunk_index, c.title, c.content, vector_literal(v),
                         Jsonb({"sha256": sha, "model": embedder.model_name}))
                        for c, v in zip(chunks, vectors, strict=True)
                    ],
                )
            result["updated" if source in stored else "added"].append(source)

        for source in sorted(set(stored) - set(files)):
            conn.execute("DELETE FROM doc_chunks WHERE source = %s", (source,))
            result["removed"].append(source)
    return result


def main() -> int:
    step = "loading settings"
    try:
        url = get_admin_settings().database_admin_url
        step = "ingesting runbooks"
        result = ingest_runbooks(url, FastEmbedEmbedder())
    except psycopg.errors.DatabaseError as exc:
        detail = exc.diag.message_primary if exc.diag else None
        print(f"ingest failed while {step}: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ingest failed while {step}: {type(exc).__name__}", file=sys.stderr)
        return 1

    for status, sources in result.items():
        print(f"{status}: {len(sources)}" + (f" ({', '.join(sources)})" if sources else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
