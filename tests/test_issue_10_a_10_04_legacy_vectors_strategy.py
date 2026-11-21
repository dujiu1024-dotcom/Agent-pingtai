"""A-10-04: enforce the legacy vectors.py / platform Knowledge boundary."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "knowledge" / "legacy-vectors-migration.md"


class LegacyVectorsMigrationPolicyTests(unittest.TestCase):
    def test_platform_knowledge_runtime_does_not_import_legacy_vectors(self):
        roots = (ROOT / "agent_platform" / "knowledge", ROOT / "agent_platform" / "api")
        scanned = []
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                if path.name == "__init__.py" or path.is_file():
                    scanned.append(path)
        forbidden = []
        for path in scanned:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name == "vectors" or name.startswith("vectors.") for name in names):
                    forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(forbidden, [], "platform runtime must not depend on legacy vectors.py")

    def test_policy_defines_freeze_reingestion_and_exit_criteria(self):
        policy = POLICY.read_text(encoding="utf-8")
        for required in (
            "freeze its feature surface",
            "content-level re-ingestion",
            "KnowledgeService.ingest",
            "VersionedEmbedder",
            "tenant/project/document/chunk ACLs",
            "read-only",
            "pgvector query alone",
        ):
            with self.subTest(required=required):
                self.assertIn(required, policy)

    def test_legacy_entrypoint_remains_separate_for_existing_consumers(self):
        legacy = (ROOT / "vectors.py").read_text(encoding="utf-8")
        for entrypoint in ("def index_path(", "def index_text(", "def search(", "def delete_by_source("):
            with self.subTest(entrypoint=entrypoint):
                self.assertIn(entrypoint, legacy)


if __name__ == "__main__":
    unittest.main()
