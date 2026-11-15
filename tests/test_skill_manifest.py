from __future__ import annotations

import unittest

from agent_platform.control_plane.skill_manifest import (
    SkillManifestError, parse_skill_document, validate_skill_bundle_paths,
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


if __name__ == "__main__":
    unittest.main()
