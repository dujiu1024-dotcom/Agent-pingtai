from __future__ import annotations

import unittest
from pathlib import Path

from agent_platform.knowledge.embedding import DEFAULT_EMBEDDING_DIMENSIONS, SUPPORTED_EMBEDDING_DIMENSIONS, EmbeddingProfile
from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository


class EmbeddingDimensionBaselineTests(unittest.TestCase):
    def test_default_profile_and_postgres_repository_use_schema_dimension(self):
        profile = EmbeddingProfile("bge-m3", "v1")
        repository = PostgresKnowledgeRepository(lambda: None)
        self.assertEqual(profile.dimensions, DEFAULT_EMBEDDING_DIMENSIONS)
        self.assertEqual(SUPPORTED_EMBEDDING_DIMENSIONS, (1024, 1536))
        self.assertEqual(repository.embedding_dimensions, 1024)

    def test_1536_dimension_profile_requires_explicit_opt_in(self):
        profile = EmbeddingProfile("legacy-model", "v1", dimensions=1536)
        self.assertEqual(profile.dimensions, 1536)
        repository = PostgresKnowledgeRepository(lambda: None, embedding_dimensions=1536)
        self.assertEqual(repository.embedding_dimensions, 1536)

    def test_rejects_unsupported_embedding_dimensions(self):
        with self.assertRaisesRegex(ValueError, "one of"):
            EmbeddingProfile("invalid", "v1", dimensions=768)
        with self.assertRaisesRegex(ValueError, "one of"):
            PostgresKnowledgeRepository(lambda: None, embedding_dimensions=768)

    def test_migration_enforces_pgvector_and_catalog_dimension_defaults(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "agent_platform"
            / "storage"
            / "postgres"
            / "104_knowledge_embedding_dimensions_1024.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("vector(1024)", migration)
        self.assertIn("ALTER COLUMN dimensions SET DEFAULT 1024", migration)
        self.assertIn("retired_at = COALESCE(retired_at, now())", migration)

    def test_dual_dimension_migration_adds_version_identity_and_dimension_indexes(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "agent_platform" / "storage" / "postgres"
            / "107_knowledge_embedding_dimension_indexes.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ALTER COLUMN embedding TYPE vector", migration)
        self.assertIn("CHECK (dimensions IN (1024, 1536))", migration)
        self.assertIn("vector_dims(embedding) = dimensions", migration)
        self.assertIn("UNIQUE (tenant_id, chunk_id, embedding_model, model_version)", migration)
        self.assertIn("knowledge_embeddings_cosine_1024_idx", migration)
        self.assertIn("knowledge_embeddings_cosine_1536_idx", migration)


if __name__ == "__main__":
    unittest.main()
