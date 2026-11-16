from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.control_plane.registry import RegistryVersion
from agent_platform.control_plane.skill_dependency_review import (
    InMemorySkillDependencyReviewStore,
    SkillDependencyReviewError,
    SkillDependencyReviewer,
)


class Scanner:
    def __init__(self, findings=()):
        self.findings = findings

    async def scan(self, package, pin):
        return self.findings


def skill(dependencies):
    return RegistryVersion(
        tenant_id="tenant-a", kind="skill", item_id="summarize",
        version=3, definition={"manifest": {
            "name": "summarize", "dependencies": dependencies,
        }}, status="published", created_by="admin",
        created_at=datetime.now(timezone.utc),
    )


class SkillDependencyReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_allowlisted_pinned_dependency_is_approved(self):
        store = InMemorySkillDependencyReviewStore()
        reviewer = SkillDependencyReviewer(
            store=store, approved_pins={"safe-parser": ("==1.2.3",)},
            scanner=Scanner(),
        )
        result = await reviewer.review(
            item=skill({"safe-parser": "==1.2.3"}), actor_id="security-admin",
        )
        self.assertEqual(result.status, "approved")
        await reviewer.require_approved(tenant_id="tenant-a", item=skill({"safe-parser": "==1.2.3"}))

    async def test_unapproved_pin_and_vulnerability_are_rejected(self):
        store = InMemorySkillDependencyReviewStore()
        reviewer = SkillDependencyReviewer(
            store=store, approved_pins={"safe-parser": ("==1.2.3",)},
            scanner=Scanner(("CVE-TEST-1",)),
        )
        result = await reviewer.review(
            item=skill({"safe-parser": "==1.2.3", "unknown": "==9.9"}),
            actor_id="security-admin",
        )
        self.assertEqual(result.status, "rejected")
        self.assertTrue(any("dependency_not_allowlisted" in item for item in result.findings))
        self.assertTrue(any("dependency_advisory" in item for item in result.findings))

    async def test_stale_manifest_cannot_reuse_previous_approval(self):
        store = InMemorySkillDependencyReviewStore()
        reviewer = SkillDependencyReviewer(
            store=store, approved_pins={"safe-parser": ("==1.2.3",)},
        )
        original = skill({"safe-parser": "==1.2.3"})
        await reviewer.review(item=original, actor_id="security-admin")
        changed = skill({"safe-parser": "==1.2.3", "new-parser": "==2.0"})
        with self.assertRaises(SkillDependencyReviewError):
            await reviewer.require_approved(tenant_id="tenant-a", item=changed)


if __name__ == "__main__":
    unittest.main()
