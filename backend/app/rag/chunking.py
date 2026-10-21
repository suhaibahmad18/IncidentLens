"""Split markdown runbooks into heading-based chunks small enough to embed and cite."""

from dataclasses import dataclass

MAX_CHUNK_CHARS = 1200


@dataclass(frozen=True)
class Chunk:
    source: str
    chunk_index: int
    title: str
    content: str


def chunk_markdown(source: str, text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """One chunk per '## ' section (split further by paragraph if too long).

    Each chunk's content starts with its title so the embedding keeps the context.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    doc_title = next((line[2:].strip() for line in lines if line.startswith("# ")), source)

    sections: list[tuple[str, list[str]]] = []
    heading, body = "Overview", []
    for line in lines:
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if any(part.strip() for part in body):
                sections.append((heading, body))
            heading, body = line[3:].strip(), []
        else:
            body.append(line)
    if any(part.strip() for part in body):
        sections.append((heading, body))

    chunks: list[Chunk] = []
    for heading, body in sections:
        title = f"{doc_title} - {heading}"
        for piece in _pack_paragraphs("\n".join(body).strip(), max_chars - len(title) - 2):
            chunks.append(Chunk(source, len(chunks), title, f"{title}\n\n{piece}"))
    return chunks


def _pack_paragraphs(text: str, limit: int) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces, current = [], ""
    for paragraph in paragraphs:
        while len(paragraph) > limit:  # a single oversized paragraph: hard split
            pieces.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > limit:
            pieces.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces
