"""Versioned embedding adapter with strict vector contract validation."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, Sequence


class EmbeddingProvider(Protocol):
    async def embed_query(self, text: str) -> Sequence[float]: ...
    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


@dataclass(frozen=True)
class EmbeddingProfile:
    model_id: str
    version: str
    dimensions: int = 1024
    normalize: bool = False

    def __post_init__(self) -> None:
        if not self.model_id.strip() or not self.version.strip():
            raise ValueError("embedding model_id and version are required")
        if isinstance(self.dimensions, bool) or self.dimensions < 1:
            raise ValueError("embedding dimensions must be a positive integer")

    @property
    def storage_key(self) -> str:
        return f"{self.model_id}@{self.version}"


class VersionedEmbedder:
    """Adapts a provider to a stable, versioned and dimension-checked contract."""

    def __init__(self, provider: EmbeddingProvider, profile: EmbeddingProfile) -> None:
        self.provider = provider
        self.profile = profile
        self.model_id = profile.model_id
        self.model_version = profile.version
        self.dimensions = profile.dimensions
        self.storage_key = profile.storage_key

    def _vector(self, values: Sequence[float]) -> list[float]:
        if len(values) != self.dimensions:
            raise ValueError(
                f"embedding dimension mismatch: expected {self.dimensions}, got {len(values)}"
            )
        vector = [float(value) for value in values]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding values must be finite")
        if self.profile.normalize:
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0:
                raise ValueError("zero embedding cannot be normalized")
            vector = [value / norm for value in vector]
        return vector

    async def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("embedding query must not be empty")
        return self._vector(await self.provider.embed_query(text))

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding documents must be non-empty strings")
        vectors = await self.provider.embed_documents(texts)
        if len(vectors) != len(texts):
            raise ValueError("embedding count must match document count")
        return [self._vector(vector) for vector in vectors]
