"""Deterministic stand-ins for external dependencies."""

import hashlib
import math
import re

from app.rag.embeddings import EMBEDDING_DIM


class FakeEmbedder:
    """Hashed bag-of-words vectors: lexical similarity, no model download, fully repeatable."""

    model_name = "fake-hashing-embedder"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    @staticmethod
    def _embed_one(text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            stem = token[:6]  # crude stemming: 'expired'/'expiry' share 'expire'/'expiry' prefixes
            index = int.from_bytes(hashlib.blake2b(stem.encode(), digest_size=4).digest(), "big")
            vector[index % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]
