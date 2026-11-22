from __future__ import annotations

import unittest
from pathlib import Path

from agent_platform.knowledge.embedding import EmbeddingProfile
from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository


class EmbeddingDimensionBaselineTests(unittest.TestCase):
    def test_default_profile_and_postgres_repository_use_schema_dimension(self):
        profile = EmbeddingProfile("bge-m3", "v1")
        repository = PostgresKnowledgeRepository(lambda: None)
        self.assertEqual(profile.dimensions, 1024)
        self.assertEqual(repository.embedding_dimensions, 1024)

    def test_1536_dimension_profile_requires_explicit_opt_in(self):
        profile = EmbeddingProfile("legacy-model", "v1", dimensions=1536)
        self.assertEqual(profile.dimensions, 1536)

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


if __name__ == "__main__":
    unittest.main()
