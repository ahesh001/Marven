"""Local embedding providers used by Marven's rebuildable projections.

The canonical memory table never depends on a provider. Switching providers
only changes the disposable ``emb`` projection, which ``MemoryManager``
rebuilds automatically when the provider identity or dimension changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import math
import os
import re
from typing import List, Optional, Sequence


class EmbeddingProvider(ABC):
    """Minimal interface for a local, replaceable embedding backend."""

    @property
    @abstractmethod
    def identity(self) -> str:
        """Stable provider/model identifier used to version the projection."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Number of values returned by :meth:`embed`."""

    @abstractmethod
    def embed(self, text: str, *, purpose: str) -> Sequence[float]:
        """Embed text for either ``query`` or ``document`` use."""


def _unit_vector(values: Sequence[float]) -> List[float]:
    clean = [float(value) for value in values]
    if not all(math.isfinite(value) for value in clean):
        raise ValueError("embedding contains a non-finite value")
    norm = math.sqrt(sum(value * value for value in clean)) or 1.0
    return [value / norm for value in clean]


class HashEmbeddingProvider(EmbeddingProvider):
    """Dependency-free lexical projection retained as the safe fallback.

    This is deliberately named ``hash`` rather than ``semantic``: it preserves
    the original Marven behavior but does not understand synonyms. SQLite FTS5
    supplies the second retrieval signal when this fallback is active.
    """

    def __init__(self, dimension: int = 256):
        if int(dimension) <= 0:
            raise ValueError("embedding dimension must be positive")
        self._dimension = int(dimension)

    @property
    def identity(self) -> str:
        return f"hash-sha256-v2:{self.dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str, *, purpose: str) -> Sequence[float]:
        del purpose
        vector = [0.0] * self.dimension
        for token in re.findall(r"[\w'-]+", text.lower()):
            digest = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
            index = digest % self.dimension
            sign = -1.0 if ((digest >> 8) & 1) else 1.0
            vector[index] += sign
        return _unit_vector(vector)


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Optional in-process learned embeddings for a fully local Marven setup."""

    def __init__(self, model_name: str = "intfloat/e5-small-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "sentence-transformers is required for the configured memory embedder"
            ) from exc

        self.model_name = str(model_name).strip()
        if not self.model_name:
            raise ValueError("embedding model name is required")
        self._model = SentenceTransformer(self.model_name)
        dimension = self._model.get_sentence_embedding_dimension()
        if not dimension:
            raise RuntimeError("embedding model did not report a dimension")
        self._dimension = int(dimension)

    @property
    def identity(self) -> str:
        return f"sentence-transformers:{self.model_name}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str, *, purpose: str) -> Sequence[float]:
        value = str(text or "")
        if "e5" in self.model_name.lower():
            prefix = "query: " if purpose == "query" else "passage: "
            value = prefix + value
        vector = self._model.encode([value], normalize_embeddings=True)[0]
        return _unit_vector(vector)


def embedding_provider_from_env(
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
) -> EmbeddingProvider:
    """Build the configured local provider without introducing cloud calls."""

    selected = (provider_name or os.environ.get("MARVEN_MEMORY_EMBEDDER", "hash")).strip().lower()
    if selected in {"hash", "hashed", "fallback"}:
        return HashEmbeddingProvider()
    if selected in {"sentence-transformers", "sentence_transformers", "st", "local"}:
        model = model_name or os.environ.get(
            "MARVEN_MEMORY_EMBEDDING_MODEL", "intfloat/e5-small-v2"
        )
        return SentenceTransformerEmbeddingProvider(model)
    raise ValueError(f"unsupported memory embedding provider: {selected}")


__all__ = [
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "SentenceTransformerEmbeddingProvider",
    "embedding_provider_from_env",
]
