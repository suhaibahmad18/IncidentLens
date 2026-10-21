"""Real FastEmbed model checks. Skipped until the fastembed package is installed."""

import math

import pytest

pytest.importorskip("fastembed")

from app.rag.embeddings import EMBEDDING_DIM, FastEmbedEmbedder  # noqa: E402

pytestmark = pytest.mark.model


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.hypot(*a) * math.hypot(*b))


def test_bge_small_produces_normalized_384_dim_vectors():
    vectors = FastEmbedEmbedder().embed(["timeout acquiring connection from pool", "certificate expired"])

    assert all(len(v) == EMBEDDING_DIM for v in vectors)
    assert all(abs(math.hypot(*v) - 1) < 1e-3 for v in vectors)


def test_semantic_similarity_ranks_related_text_higher():
    query, related, unrelated = FastEmbedEmbedder().embed([
        "TLS handshake fails because the server certificate is no longer valid",
        "x509: certificate has expired or is not yet valid",
        "container OOMKilled: memory limit exceeded",
    ])

    assert cosine(query, related) > cosine(query, unrelated)
