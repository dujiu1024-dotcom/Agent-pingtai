from __future__ import annotations

import unittest

from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.control_plane.skill_dependency_review import (
    InMemorySkillDependencyReviewStore,
    SkillDependencyReviewer,
)
from agent_platform.control_plane.skill_registry import (
    InMemoryAgentSkillStore,
    SkillPackageRegistry,
)
from agent_platform.control_plane.skill_sandbox import (
    InMemorySkillArtifactStore,
    SkillSandboxError,
)
from tests.test_issue_28_a_28_01_skill_lifecycle_acceptance import (
    DependencyScanner,
    PackageArtifacts,
    RecordingSandboxRunner,
    SandboxBackedAgentRuntime,
    skill_package,
)


class VerdictScanner:
    def __init__(self, verdict=True, error=None) -> None:
        self.verdict = verdict
        self.error = error
        self.calls: list[tuple[str, str, bytes]] = []

    async def scan(self, filename: str, media_type: str, content: bytes):
        self.calls.append((filename, media_type, content))
        if self.error is not None:
            raise self.error
        return self.verdict


class MaliciousSkillInstallAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.package_artifacts = PackageArtifacts()
        self.agent_store = InMemoryAgentSkillStore()
        self.sandbox_artifacts = InMemorySkillArtifactStore()
        self.sandbox_runner = RecordingSandboxRunner()
        self.scanner = VerdictScanner()
        self.sandbox_audit: list[tuple[str, str, str]] = []
        from agent_platform.control_plane.skill_sandbox import SkillSandboxRuntime

        self.sandbox = SkillSandboxRuntime(
            tenant_id="tenant-a",
            artifact_store=self.sandbox_artifacts,
            scanner=self.scanner,
            sandbox_runner=self.sandbox_runner,
            available_tools={"knowledge.search"},
            audit=lambda actor, skill, action, status: self.sandbox_audit.append(
                (skill, action, status)
            ),
        )
        self.agent_runtime = SandboxBackedAgentRuntime(
            artifacts=self.package_artifacts,
            sandbox=self.sandbox,
        )
        self.registry = RegistryService(InMemoryRegistryRepository())
        self.dependency_reviewer = SkillDependencyReviewer(
            store=InMemorySkillDependencyReviewStore(),
            approved_pins={"safe-parser": ("==1.2.3",)},
            scanner=DependencyScanner(),
        )
        self.audit: list[tuple[str, str, str, str, str]] = []
        self.service = SkillPackageRegistry(
            registry=self.registry,
            artifacts=self.package_artifacts,
            dependency_reviewer=self.dependency_reviewer,
            available_tools={"knowledge.search"},
            agent_store=self.agent_store,
            agent_runtime=self.agent_runtime,
            audit=lambda *event: self.audit.append(event),
        )

    async def _publish_package(self) -> tuple[int, str, bytes]:
        archive = skill_package()
        draft = await self.service.upload(
            tenant_id="tenant-a", archive=archive, actor_id="publisher",
        )
        published = await self.service.publish(
            tenant_id="tenant-a",
            skill_id="report-summarizer",
            draft_version=draft.version,
            actor_id="publisher",
        )
        return published.version, published.definition["artifact_ref"], archive

    async def _assert_install_blocked(self, *, expected_audit: str) -> None:
        version, artifact_ref, archive = await self._publish_package()
        with self.assertRaises(SkillSandboxError):
            await self.service.install_for_agent(
                tenant_id="tenant-a",
                agent_id="agent-malware-test",
                skill_id="report-summarizer",
                version=version,
                actor_id="agent-admin",
            )

        key = ("tenant-a", "agent-malware-test", "report-summarizer")
        self.assertEqual(
            self.scanner.calls,
            [("report-summarizer.zip", "application/zip", archive)],
        )
        self.assertIsNone(await self.agent_store.get(*key))
        self.assertNotIn(key, self.agent_runtime.versions)
        self.assertIsNone(
            await self.sandbox_artifacts.get("tenant-a", "report-summarizer", version)
        )
        self.assertEqual(self.sandbox_runner.calls, [])
        self.assertIn(artifact_ref, self.package_artifacts.items)
        self.assertEqual(self.sandbox_audit[-1], ("report-summarizer", "install", expected_audit))

    async def test_malicious_scan_verdict_blocks_agent_install_without_side_effects(self) -> None:
        self.scanner.verdict = False
        await self._assert_install_blocked(expected_audit="infected")

    async def test_scanner_outage_blocks_agent_install_without_side_effects(self) -> None:
        self.scanner.error = OSError("scanner unavailable")
        await self._assert_install_blocked(expected_audit="scan_error")

    async def test_non_boolean_scanner_verdict_is_not_accepted_as_clean(self) -> None:
        self.scanner.verdict = "clean"
        await self._assert_install_blocked(expected_audit="infected")


if __name__ == "__main__":
    unittest.main()
