from __future__ import annotations

import unittest

from agent_platform.knowledge.embedding import EmbeddingProfile, VersionedEmbedder


class Provider:
    async def embed_query(self, text):
        return [3.0, 4.0]

    async def embed_documents(self, texts):
        return [[3.0, 4.0] for _ in texts]


class BrokenProvider(Provider):
    async def embed_query(self, text):
        return [1.0]


class EmbeddingAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_key_and_normalization(self):
        embedder = VersionedEmbedder(
            Provider(), EmbeddingProfile("demo", "2026-01", dimensions=2, normalize=True)
        )
        self.assertEqual(embedder.storage_key, "demo@2026-01")
        self.assertEqual(await embedder.embed_query("hello"), [0.6, 0.8])
        self.assertEqual(await embedder.embed_documents(["hello"]), [[0.6, 0.8]])

    async def test_rejects_dimension_mismatch(self):
        embedder = VersionedEmbedder(BrokenProvider(), EmbeddingProfile("demo", "v1", 2))
        with self.assertRaisesRegex(ValueError, "dimension mismatch"):
            await embedder.embed_query("hello")

    async def test_rejects_empty_input_and_non_finite_values(self):
        embedder = VersionedEmbedder(Provider(), EmbeddingProfile("demo", "v1", 2))
        with self.assertRaises(ValueError):
            await embedder.embed_query(" ")
        class NonFinite(Provider):
            async def embed_query(self, text):
                return [float("nan"), 0.0]
        with self.assertRaisesRegex(ValueError, "finite"):
            await VersionedEmbedder(NonFinite(), EmbeddingProfile("demo", "v1", 2)).embed_query("x")
