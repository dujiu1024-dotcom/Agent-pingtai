from __future__ import annotations

import base64
import io
import json
import unittest
import zipfile
from types import SimpleNamespace

from agent_platform.control_plane.registry import (
    InMemoryRegistryRepository,
    RegistryService,
    RegistryVersion,
)
from agent_platform.control_plane.skill_dependency_review import (
    InMemorySkillDependencyReviewStore,
    SkillDependencyReviewer,
)
from agent_platform.control_plane.skill_registry import (
    AgentSkillInstallation,
    InMemoryAgentSkillStore,
    SkillPackageRegistry,
)
from agent_platform.control_plane.skill_sandbox import (
    InMemorySkillArtifactStore,
    SkillSandboxRuntime,
)
from agent_platform.sandbox.oci import SandboxResult

PACKAGE_MANIFEST = """name: report-summarizer
version: 1.2.0
description: Summarize reports with citations.
entrypoint: SKILL.md
dependencies:
  safe-parser: ==1.2.3
allowed-tools:
  - knowledge.search
"""

SKILL_DOCUMENT = """---
name: report-summarizer
description: Summarize reports with citations.
allowed-tools: [knowledge.search]
---
Read the source and cite every factual claim.
"""


def skill_package() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.yaml", PACKAGE_MANIFEST)
        archive.writestr("SKILL.md", SKILL_DOCUMENT)
    return output.getvalue()


class PackageArtifacts:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    async def push(self, *, tenant_id: str, skill_id: str, version: str, content: bytes) -> str:
        reference = f"oci://registry/{tenant_id}/{skill_id}:{version}"
        self.items[reference] = bytes(content)
        return reference

    async def pull(self, artifact_ref: str) -> bytes:
        return self.items[artifact_ref]

    async def delete(self, artifact_ref: str) -> None:
        self.items.pop(artifact_ref, None)


class FaultInjectingAgentSkillStore(InMemoryAgentSkillStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_put = False
        self.fail_next_remove = False

    async def put(self, item: AgentSkillInstallation) -> None:
        if self.fail_next_put:
            self.fail_next_put = False
            raise RuntimeError("injected Agent skill store put failure")
        await super().put(item)

    async def remove(self, tenant_id: str, agent_id: str, skill_id: str) -> None:
        if self.fail_next_remove:
            self.fail_next_remove = False
            raise RuntimeError("injected Agent skill store remove failure")
        await super().remove(tenant_id, agent_id, skill_id)


class DependencyScanner:
    async def scan(self, package: str, pin: str) -> tuple[str, ...]:
        return ()


class BundleScanner:
    async def scan(self, filename: str, media_type: str, content: bytes) -> bool:
        return filename.endswith(".zip") and media_type == "application/zip" and bool(content)


class RecordingSandboxRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bytes]] = []

    async def run(self, command, *, stdin=b"", environment=None, timeout_seconds=30):
        self.calls.append((tuple(command), stdin))
        request = json.loads(stdin)
        archive_bytes = base64.b64decode(request["archive"], validate=True)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            document = archive.read("SKILL.md").decode("utf-8")
        return SandboxResult(0, json.dumps({"skill_md": document}).encode("utf-8"), b"")


class SandboxBackedAgentRuntime:
    """Bridge registry Agent installs to the real SkillSandboxRuntime contract."""

    def __init__(self, *, artifacts: PackageArtifacts, sandbox: SkillSandboxRuntime) -> None:
        self.artifacts = artifacts
        self.sandbox = sandbox
        self.versions: dict[tuple[str, str, str], int] = {}

    async def install(self, *, tenant_id: str, agent_id: str, skill_id: str,
                      version: int, artifact_ref: str) -> None:
        archive = await self.artifacts.pull(artifact_ref)
        await self.sandbox.install(
            skill_id=skill_id, version=version, archive=archive,
            actor=agent_id, tenant_id=tenant_id,
        )
        self.versions[(tenant_id, agent_id, skill_id)] = version

    async def uninstall(self, *, tenant_id: str, agent_id: str, skill_id: str) -> None:
        key = (tenant_id, agent_id, skill_id)
        version = self.versions.pop(key)
        principal = SimpleNamespace(tenant_id=tenant_id, user_id=agent_id)
        await self.sandbox.uninstall(
            skill_id=skill_id, version=version, principal=principal, tenant_id=tenant_id,
        )

    async def load(self, *, tenant_id: str, agent_id: str, skill_id: str):
        version = self.versions[(tenant_id, agent_id, skill_id)]
        principal = SimpleNamespace(tenant_id=tenant_id, user_id=agent_id)
        return await self.sandbox.load(
            skill_id=skill_id, version=version, principal=principal, tenant_id=tenant_id,
        )


class SkillLifecycleAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.package_artifacts = PackageArtifacts()
        self.agent_store = FaultInjectingAgentSkillStore()
        self.sandbox_artifacts = InMemorySkillArtifactStore()
        self.sandbox_runner = RecordingSandboxRunner()
        self.sandbox_audit: list[tuple[str, str, str]] = []
        self.sandbox = SkillSandboxRuntime(
            tenant_id="tenant-a",
            artifact_store=self.sandbox_artifacts,
            scanner=BundleScanner(),
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

    async def _publish_test_skill(self) -> RegistryVersion:
        draft = await self.service.upload(
            tenant_id="tenant-a", archive=skill_package(), actor_id="publisher",
        )
        return await self.service.publish(
            tenant_id="tenant-a",
            skill_id="report-summarizer",
            draft_version=draft.version,
            actor_id="publisher",
        )

    async def test_install_store_failure_compensates_runtime_and_leaves_no_residue(self) -> None:
        published = await self._publish_test_skill()
        artifact_ref = published.definition["artifact_ref"]
        self.agent_store.fail_next_put = True

        with self.assertRaisesRegex(RuntimeError, "injected Agent skill store put failure"):
            await self.service.install_for_agent(
                tenant_id="tenant-a",
                agent_id="agent-rollback",
                skill_id="report-summarizer",
                version=published.version,
                actor_id="agent-admin",
            )

        key = ("tenant-a", "agent-rollback", "report-summarizer")
        self.assertIsNone(await self.agent_store.get(*key))
        self.assertNotIn(key, self.agent_runtime.versions)
        self.assertIsNone(await self.sandbox_artifacts.get(
            "tenant-a", "report-summarizer", published.version,
        ))
        self.assertIn(artifact_ref, self.package_artifacts.items)
        self.assertEqual(
            [event[4] for event in self.audit if event[3] == "install"],
            ["requested", "compensated"],
        )

    async def test_uninstall_store_failure_restores_installation_and_runtime(self) -> None:
        published = await self._publish_test_skill()
        key = ("tenant-a", "agent-rollback", "report-summarizer")
        installation = await self.service.install_for_agent(
            tenant_id=key[0],
            agent_id=key[1],
            skill_id=key[2],
            version=published.version,
            actor_id="agent-admin",
        )
        self.agent_store.fail_next_remove = True

        with self.assertRaisesRegex(RuntimeError, "injected Agent skill store remove failure"):
            await self.service.uninstall_from_agent(
                tenant_id=key[0],
                agent_id=key[1],
                skill_id=key[2],
                actor_id="agent-admin",
            )

        self.assertEqual(await self.agent_store.get(*key), installation)
        self.assertEqual(self.agent_runtime.versions[key], published.version)
        self.assertIsNotNone(await self.sandbox_artifacts.get(
            key[0], key[2], published.version,
        ))
        loaded = await self.agent_runtime.load(
            tenant_id=key[0], agent_id=key[1], skill_id=key[2],
        )
        self.assertEqual(loaded.version, published.version)
        self.assertEqual(
            [event[4] for event in self.audit if event[3] == "uninstall"],
            ["requested", "compensated"],
        )

    async def test_publish_install_load_uninstall_leaves_no_agent_runtime_residue(self) -> None:
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
        self.assertEqual(published.status, "published")
        artifact_ref = published.definition["artifact_ref"]
        self.assertIn(artifact_ref, self.package_artifacts.items)

        installation = await self.service.install_for_agent(
            tenant_id="tenant-a",
            agent_id="agent-1",
            skill_id="report-summarizer",
            version=published.version,
            actor_id="agent-admin",
        )
        self.assertEqual(installation.artifact_ref, artifact_ref)
        self.assertIsNotNone(
            await self.agent_store.get("tenant-a", "agent-1", "report-summarizer")
        )
        self.assertIsNotNone(
            await self.sandbox_artifacts.get(
                "tenant-a", "report-summarizer", published.version,
            )
        )

        loaded = await self.agent_runtime.load(
            tenant_id="tenant-a", agent_id="agent-1", skill_id="report-summarizer",
        )
        self.assertEqual(loaded.instructions, "Read the source and cite every factual claim.")
        self.assertEqual(loaded.version, published.version)
        self.assertEqual(len(self.sandbox_runner.calls), 1)
        self.assertEqual(self.sandbox_runner.calls[0][0][:3], ("python", "-I", "-c"))

        await self.service.uninstall_from_agent(
            tenant_id="tenant-a",
            agent_id="agent-1",
            skill_id="report-summarizer",
            actor_id="agent-admin",
        )

        self.assertIsNone(
            await self.agent_store.get("tenant-a", "agent-1", "report-summarizer")
        )
        self.assertIsNone(
            await self.sandbox_artifacts.get(
                "tenant-a", "report-summarizer", published.version,
            )
        )
        self.assertNotIn(
            ("tenant-a", "agent-1", "report-summarizer"),
            self.agent_runtime.versions,
        )
        with self.assertRaises(LookupError):
            await self.agent_runtime.load(
                tenant_id="tenant-a", agent_id="agent-1", skill_id="report-summarizer",
            )

        # Uninstall removes the Agent's runtime copy, not the immutable published
        # package needed by other Agents; this is not residual Agent state.
        self.assertIn(artifact_ref, self.package_artifacts.items)
        self.assertIsNotNone(
            await self.registry.repository.get_active(
                "tenant-a", "skill", "report-summarizer",
            )
        )
        self.assertEqual(
            [event[4] for event in self.audit if event[3] == "install"],
            ["requested", "succeeded"],
        )
        self.assertEqual(
            [event[4] for event in self.audit if event[3] == "uninstall"],
            ["requested", "succeeded"],
        )
        self.assertEqual(
            [event[2] for event in self.sandbox_audit],
            ["scanning", "installed", "requested", "loaded", "uninstalled"],
        )


if __name__ == "__main__":
    unittest.main()
