"""Text embeddings. Production uses local FastEmbed; tests inject a deterministic fake."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384  # must match doc_chunks.embedding vector(384)
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "fastembed"


class Embedder(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    """Loads the ONNX model on first use (downloaded once into the cache directory)."""

    model_name = EMBEDDING_MODEL

    def __init__(self, cache_dir: str | os.PathLike | None = None):
        self._cache_dir = str(cache_dir or os.environ.get("FASTEMBED_CACHE_PATH") or DEFAULT_CACHE_DIR)
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from fastembed import TextEmbedding  # heavy import, only when actually needed

            self._model = TextEmbedding(model_name=EMBEDDING_MODEL, cache_dir=self._cache_dir)
        vectors = [vector.tolist() for vector in self._model.embed(texts)]
        if any(len(vector) != EMBEDDING_DIM for vector in vectors):
            raise ValueError(f"{EMBEDDING_MODEL} returned an unexpected embedding size")
        return vectors


@lru_cache
def get_embedder() -> FastEmbedEmbedder:
    return FastEmbedEmbedder()


def vector_literal(vector: list[float]) -> str:
    """pgvector text format, e.g. '[0.1,0.2]'. Used with an explicit ::vector cast."""
    if len(vector) != EMBEDDING_DIM:
        raise ValueError(f"expected {EMBEDDING_DIM} dimensions, got {len(vector)}")
    return "[" + ",".join(f"{value:.7f}" for value in vector) + "]"
