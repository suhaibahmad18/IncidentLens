import shutil

import pytest

from app.rag.ingest import RUNBOOKS_DIR, ingest_runbooks
from app.rag.retrieval import search_runbooks
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.db


def test_all_runbooks_ingested_with_384_dim_embeddings(admin_conn):
    sources, chunks, dims = admin_conn.execute(
        "SELECT count(DISTINCT source), count(*), min(vector_dims(embedding)) FROM doc_chunks"
    ).fetchone()

    assert sources == len(list(RUNBOOKS_DIR.glob("*.md")))
    assert chunks >= sources
    assert dims == 384


def test_reingesting_unchanged_runbooks_does_nothing(test_db):
    result = ingest_runbooks(test_db["admin"], FakeEmbedder())

    assert result["added"] == result["updated"] == result["removed"] == []
    assert len(result["unchanged"]) == len(list(RUNBOOKS_DIR.glob("*.md")))


def test_ingest_detects_added_changed_and_removed_files(test_db, tmp_path, admin_conn):
    # A separate source prefix keeps this test away from the real runbooks.
    prefix = "test-runbooks/"
    for name in ("general-incident-triage.md", "deployment-rollback.md"):
        shutil.copy(RUNBOOKS_DIR / name, tmp_path / name)
    try:
        first = ingest_runbooks(test_db["admin"], FakeEmbedder(), tmp_path, prefix)
        assert sorted(first["added"]) == [f"{prefix}deployment-rollback.md", f"{prefix}general-incident-triage.md"]

        (tmp_path / "deployment-rollback.md").write_text("# Changed\n\n## Body\nNew text.\n", encoding="utf-8")
        (tmp_path / "general-incident-triage.md").unlink()
        second = ingest_runbooks(test_db["admin"], FakeEmbedder(), tmp_path, prefix)

        assert second["updated"] == [f"{prefix}deployment-rollback.md"]
        assert second["removed"] == [f"{prefix}general-incident-triage.md"]
        content = admin_conn.execute(
            "SELECT string_agg(content, ' ') FROM doc_chunks WHERE source = %s",
            (f"{prefix}deployment-rollback.md",),
        ).fetchone()[0]
        assert "New text." in content
    finally:
        for path in tmp_path.glob("*.md"):
            path.unlink()
        ingest_runbooks(test_db["admin"], FakeEmbedder(), tmp_path, prefix)  # removes the test sources


@pytest.mark.parametrize(
    ("query", "expected_source"),
    [
        ("x509 certificate has expired TLS handshake failed", "runbooks/tls-certificate-expiry.md"),
        ("timeout acquiring connection from pool too many connections", "runbooks/database-connection-pool-exhaustion.md"),
        ("container OOMKilled memory limit exceeded heap", "runbooks/memory-leak-oomkill.md"),
        ("429 too many requests provider rate limit queue backlog", "runbooks/queue-backlog-rate-limiting.md"),
    ],
)
def test_search_finds_the_matching_runbook(ro_conn, query, expected_source):
    hits = search_runbooks(ro_conn, FakeEmbedder(), query, top_k=3)

    assert hits[0].source == expected_source
    assert hits[0].evidence_id.startswith("runbook:") and "#" in hits[0].evidence_id
    assert hits == sorted(hits, key=lambda h: -h.score)
