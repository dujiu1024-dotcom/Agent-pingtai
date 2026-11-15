from __future__ import annotations

import unittest

from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.control_plane.skill_manifest import (
    SkillManifestError, create_skill_draft, parse_skill_document,
    validate_skill_bundle_paths,
)


class SkillManifestTests(unittest.TestCase):
    def test_parse_valid_manifest_and_exact_dependencies(self):
        document = """---
name: summarize-report
description: Summarize a report with citations.
allowed-tools:
  - knowledge.search
metadata:
  dependencies:
    internal-parser: ==1.2.3
---
Read the source, extract supported claims, and cite every conclusion.
"""
        manifest = parse_skill_document(
            document, available_tools={"knowledge.search", "blackboard.write"},
        )
        self.assertEqual(manifest.name, "summarize-report")
        self.assertEqual(manifest.allowed_tools, ("knowledge.search",))
        self.assertEqual(manifest.dependencies["internal-parser"], "==1.2.3")

    def test_unknown_tools_and_unpinned_dependencies_are_rejected(self):
        with self.assertRaises(SkillManifestError):
            parse_skill_document(
                "---\nname: unsafe\ndescription: test\nallowed-tools: [shell.exec]\n---\nDo work",
                available_tools={"knowledge.search"},
            )
        with self.assertRaises(SkillManifestError):
            parse_skill_document(
                "---\nname: unsafe\ndescription: test\nmetadata:\n  dependencies:\n    package: latest\n---\nDo work",
                available_tools=set(),
            )

    def test_duplicate_frontmatter_keys_are_rejected(self):
        with self.assertRaises(SkillManifestError):
            parse_skill_document(
                "---\nname: first\nname: second\ndescription: test\n---\nInstructions",
                available_tools=set(),
            )

    def test_bundle_paths_reject_traversal_and_require_root_manifest(self):
        self.assertEqual(
            validate_skill_bundle_paths(("SKILL.md", "references/guide.md")),
            ("SKILL.md", "references/guide.md"),
        )
        for paths in (
            ("SKILL.md", "../escape.txt"),
            ("SKILL.md", "./nested/file.md"),
            ("references/guide.md",),
            ("SKILL.md", "nested\\windows.txt"),
        ):
            with self.assertRaises(SkillManifestError):
                validate_skill_bundle_paths(paths)


class SkillRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_skill_is_saved_as_declarative_registry_draft(self):
        registry = RegistryService(InMemoryRegistryRepository())
        document = "---\nname: safe-skill\ndescription: A safe skill.\nallowed-tools: [knowledge.search]\n---\nUse cited sources only."
        saved = await create_skill_draft(
            registry=registry, tenant_id="tenant-a", item_id="safe-skill",
            document=document, bundle_paths=("SKILL.md", "references/guide.md"),
            available_tools={"knowledge.search"}, actor_id="admin",
        )
        self.assertEqual(saved.kind, "skill")
        self.assertEqual(saved.definition["format"], "agent-skills")
        self.assertEqual(saved.definition["manifest"]["allowed_tools"], ["knowledge.search"])


if __name__ == "__main__":
    unittest.main()
