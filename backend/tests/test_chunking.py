from app.rag.chunking import chunk_markdown
from app.rag.ingest import RUNBOOKS_DIR

DOC = """# Pool exhaustion

## Symptoms
- timeouts acquiring connections

## How to confirm
1. Compare with deployments.
"""


def test_one_chunk_per_section_with_title_context():
    chunks = chunk_markdown("runbooks/pool.md", DOC)

    assert [c.title for c in chunks] == ["Pool exhaustion - Symptoms", "Pool exhaustion - How to confirm"]
    assert [c.chunk_index for c in chunks] == [0, 1]
    assert chunks[0].content.startswith("Pool exhaustion - Symptoms\n\n- timeouts")


def test_long_sections_are_split_within_limit():
    paragraphs = "\n\n".join(f"Paragraph {i} " + "x" * 300 for i in range(10))
    chunks = chunk_markdown("runbooks/long.md", f"# Long\n\n## Body\n{paragraphs}", max_chars=800)

    assert len(chunks) > 1
    assert all(len(c.content) <= 800 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunking_is_deterministic_and_handles_crlf():
    assert chunk_markdown("a", DOC) == chunk_markdown("a", DOC.replace("\n", "\r\n"))


def test_every_real_runbook_chunks_cleanly():
    for path in RUNBOOKS_DIR.glob("*.md"):
        chunks = chunk_markdown(f"runbooks/{path.name}", path.read_text(encoding="utf-8"))
        assert chunks, path.name
        assert all(c.content.strip() and len(c.content) <= 1200 for c in chunks)
