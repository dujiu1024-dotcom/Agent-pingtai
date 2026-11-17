from __future__ import annotations

import io
import unittest
import zipfile

from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.control_plane.skill_dependency_review import (
    InMemorySkillDependencyReviewStore, SkillDependencyReviewer, SkillDependencyReviewError,
)
from agent_platform.control_plane.skill_registry import (
    InMemoryAgentSkillStore, SkillPackageError, SkillPackageRegistry,
    parse_skill_package_manifest,
)


MANIFEST = """name: report-summarizer
version: 1.2.0
description: Summarize reports with citations.
entrypoint: SKILL.md
dependencies:
  safe-parser: ==1.2.3
allowed-tools:
  - knowledge.search
"""
def package(manifest: str = MANIFEST) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.yaml", manifest)
        archive.writestr("SKILL.md", "Read the source and cite claims.")
    return out.getvalue()


class Artifacts:
    def __init__(self):
        self.items = {}
    async def push(self, *, tenant_id, skill_id, version, content):
        ref = f"oci://skills/{tenant_id}/{skill_id}:{version}"
        self.items[ref] = content
        return ref
    async def pull(self, artifact_ref):
        return self.items[artifact_ref]
    async def delete(self, artifact_ref):
        self.items.pop(artifact_ref, None)


class Scanner:
    def __init__(self, findings=()):
        self.findings = findings
    async def scan(self, package, pin):
        return self.findings


class Runtime:
    def __init__(self):
        self.events = []
    async def install(self, **values):
        self.events.append(("install", values))
    async def uninstall(self, **values):
        self.events.append(("uninstall", values))


class SkillPackageRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry = RegistryService(InMemoryRegistryRepository())
        self.artifacts = Artifacts()
        self.review_store = InMemorySkillDependencyReviewStore()
        self.reviewer = SkillDependencyReviewer(
            store=self.review_store, approved_pins={"safe-parser": ("==1.2.3",)},
            scanner=Scanner(),
        )
        self.agent_store = InMemoryAgentSkillStore()
        self.runtime = Runtime()
        self.events = []
        self.service = SkillPackageRegistry(
            registry=self.registry, artifacts=self.artifacts,
            dependency_reviewer=self.reviewer, available_tools={"knowledge.search"},
            agent_store=self.agent_store, agent_runtime=self.runtime,
            audit=lambda *event: self.events.append(event),
        )

    async def _published(self):
        draft = await self.service.upload(
            tenant_id="tenant-a", archive=package(), actor_id="publisher",
        )
        return await self.service.publish(
            tenant_id="tenant-a", skill_id="report-summarizer",
            draft_version=draft.version, actor_id="publisher",
        )

    async def test_manifest_requires_pins_and_existing_entrypoint(self):
        parsed = parse_skill_package_manifest(MANIFEST, available_tools={"knowledge.search"})
        self.assertEqual(parsed.version, "1.2.0")
        with self.assertRaises(SkillPackageError):
            parse_skill_package_manifest(MANIFEST.replace("==1.2.3", "latest"),
                                         available_tools={"knowledge.search"})
        with self.assertRaises(SkillPackageError):
            self.service._inspect(package(MANIFEST.replace("SKILL.md", "missing.md", 1)))

    async def test_upload_publish_and_agent_install_uninstall_lifecycle(self):
        published = await self._published()
        self.assertEqual(published.status, "published")
        installed = await self.service.install_for_agent(
            tenant_id="tenant-a", agent_id="agent-1", skill_id="report-summarizer",
            version=published.version, actor_id="agent-admin",
        )
        self.assertEqual(installed.package_version, "1.2.0")
        self.assertIsNotNone(await self.agent_store.get("tenant-a", "agent-1", "report-summarizer"))
        await self.service.uninstall_from_agent(
            tenant_id="tenant-a", agent_id="agent-1", skill_id="report-summarizer",
            actor_id="agent-admin",
        )
        self.assertIsNone(await self.agent_store.get("tenant-a", "agent-1", "report-summarizer"))
        self.assertEqual([event[0] for event in self.runtime.events], ["install", "uninstall"])

    async def test_duplicate_package_version_is_rejected_before_artifact_push(self):
        await self._published()
        before = len(self.artifacts.items)
        with self.assertRaisesRegex(SkillPackageError, "already registered"):
            await self.service.upload(
                tenant_id="tenant-a", archive=package(), actor_id="publisher",
            )
        self.assertEqual(len(self.artifacts.items), before)

    async def test_dependency_rejection_prevents_publication(self):
        bad = package(MANIFEST.replace("safe-parser: ==1.2.3", "unsafe-parser: ==9.9.9"))
        draft = await self.service.upload(tenant_id="tenant-a", archive=bad, actor_id="publisher")
        with self.assertRaises(SkillDependencyReviewError):
            await self.service.publish(
                tenant_id="tenant-a", skill_id="report-summarizer",
                draft_version=draft.version, actor_id="publisher",
            )
        active = await self.registry.repository.get_active("tenant-a", "skill", "report-summarizer")
        self.assertIsNone(active)

    async def test_agent_installation_is_tenant_and_agent_scoped(self):
        published = await self._published()
        await self.service.install_for_agent(
            tenant_id="tenant-a", agent_id="agent-1", skill_id="report-summarizer",
            version=published.version, actor_id="admin",
        )
        self.assertIsNone(await self.agent_store.get("tenant-b", "agent-1", "report-summarizer"))
        self.assertIsNone(await self.agent_store.get("tenant-a", "agent-2", "report-summarizer"))

    async def test_upload_rejects_traversal_and_duplicate_archive_members(self):
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("manifest.yaml", MANIFEST)
            archive.writestr("../escape", "x")
        with self.assertRaises(SkillPackageError):
            self.service._inspect(bad.getvalue())

    async def test_unavailable_dependency_scanner_fails_closed(self):
        reviewer = SkillDependencyReviewer(
            store=InMemorySkillDependencyReviewStore(),
            approved_pins={"safe-parser": ("==1.2.3",)}, scanner=None,
        )
        service = SkillPackageRegistry(
            registry=self.registry, artifacts=self.artifacts, dependency_reviewer=reviewer,
            available_tools={"knowledge.search"}, agent_store=self.agent_store,
            agent_runtime=self.runtime,
        )
        draft = await service.upload(tenant_id="tenant-a", archive=package(), actor_id="publisher")
        with self.assertRaises(SkillDependencyReviewError):
            await service.publish(tenant_id="tenant-a", skill_id="report-summarizer",
                                  draft_version=draft.version, actor_id="publisher")


if __name__ == "__main__":
    unittest.main()
