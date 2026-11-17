from __future__ import annotations

import io
import json
import unittest
import zipfile

from agent_platform.control_plane.skill_sandbox import (
    InMemorySkillArtifactStore, SkillSandboxError, SkillSandboxRuntime, _BOOTSTRAP,
)
from agent_platform.sandbox.oci import SandboxResult


class Scanner:
    def __init__(self, clean=True, error=None):
        self.clean, self.error, self.calls = clean, error, []
    async def scan(self, filename, media_type, content):
        self.calls.append((filename, media_type, content))
        if self.error:
            raise self.error
        return self.clean


class Runner:
    def __init__(self, *, result=None):
        self.result = result
        self.calls = []
    async def run(self, command, *, stdin=b"", environment=None, timeout_seconds=30):
        self.calls.append((command, stdin, environment, timeout_seconds))
        if self.result is not None:
            return self.result
        request = json.loads(stdin)
        with zipfile.ZipFile(io.BytesIO(__import__("base64").b64decode(request["archive"]))) as bundle:
            document = bundle.read("SKILL.md").decode()
        return SandboxResult(0, json.dumps({"skill_md": document}).encode(), b"")


def archive(document=None, extra=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr("SKILL.md", document or "---\nname: summarize\ndescription: Summarize reports.\nallowed-tools: [knowledge.search]\n---\nRead and cite sources.")
        for name, content in (extra or {}).items():
            bundle.writestr(name, content)
    return data.getvalue()


class SkillSandboxRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store, self.scanner, self.runner = InMemorySkillArtifactStore(), Scanner(), Runner()
        self.audit = []
        self.runtime = SkillSandboxRuntime(
            tenant_id="tenant-a", artifact_store=self.store, scanner=self.scanner,
            sandbox_runner=self.runner, available_tools={"knowledge.search"},
            audit=lambda actor, skill, action, status: self.audit.append((skill, action, status)),
        )
        self.principal = type("Principal", (), {"tenant_id": "tenant-a"})()

    def test_bootstrap_is_valid_python_and_does_not_execute_skill_files(self):
        compile(_BOOTSTRAP, "<skill-sandbox>", "exec")
        self.assertNotIn("exec(", _BOOTSTRAP)
        self.assertNotIn("runpy", _BOOTSTRAP)

    async def test_install_load_and_uninstall_lifecycle(self):
        document = "---\nname: summarize\ndescription: Summarize reports.\nallowed-tools: [knowledge.search]\n---\nRead and cite sources."
        manifest = await self.runtime.install(
            skill_id="summarize", version=1, archive=archive(document), actor="admin",
        )
        self.assertEqual(manifest.name, "summarize")
        loaded = await self.runtime.load(skill_id="summarize", version=1, principal=self.principal)
        self.assertEqual(loaded.instructions, "Read and cite sources.")
        self.assertEqual(self.runner.calls[0][0][:3], ("python", "-I", "-c"))
        self.assertEqual(len(self.scanner.calls), 2)
        await self.runtime.uninstall(skill_id="summarize", version=1, principal=self.principal)
        self.assertIsNone(await self.store.get("tenant-a", "summarize", 1))

    async def test_malware_and_scanner_outage_fail_closed(self):
        self.scanner.clean = False
        with self.assertRaisesRegex(SkillSandboxError, "malicious"):
            await self.runtime.install(skill_id="summarize", version=1, archive=archive(), actor="admin")
        self.scanner.clean = True
        self.scanner.error = OSError("scanner offline")
        with self.assertRaisesRegex(SkillSandboxError, "scan failed"):
            await self.runtime.install(skill_id="summarize", version=1, archive=archive(), actor="admin")
        self.assertIsNone(await self.store.get("tenant-a", "summarize", 1))

    async def test_tenant_isolation_and_uninstalled_skill(self):
        with self.assertRaises(PermissionError):
            await self.runtime.install(skill_id="summarize", version=1, archive=archive(), actor="admin", tenant_id="tenant-b")
        foreign = type("Principal", (), {"tenant_id": "tenant-b"})()
        with self.assertRaises(PermissionError):
            await self.runtime.load(skill_id="summarize", version=1, principal=foreign)
        with self.assertRaises(LookupError):
            await self.runtime.load(skill_id="summarize", version=1, principal=self.principal)

    async def test_load_rescans_and_blocks_newly_detected_malware(self):
        await self.runtime.install(skill_id="summarize", version=1, archive=archive(), actor="admin")
        self.scanner.clean = False
        with self.assertRaisesRegex(SkillSandboxError, "re-scan detected"):
            await self.runtime.load(skill_id="summarize", version=1, principal=self.principal)
        self.assertEqual(len(self.runner.calls), 0)

    async def test_archive_traversal_and_manifest_tool_escalation_are_rejected(self):
        with self.assertRaises(SkillSandboxError):
            await self.runtime.install(skill_id="summarize", version=1, archive=archive(extra={"../escape": "bad"}), actor="admin")
        unsafe = "---\nname: summarize\ndescription: bad\nallowed-tools: [shell.exec]\n---\nrun arbitrary shell"
        with self.assertRaises(ValueError):
            await self.runtime.install(skill_id="summarize", version=1, archive=archive(unsafe), actor="admin")


if __name__ == "__main__":
    unittest.main()
