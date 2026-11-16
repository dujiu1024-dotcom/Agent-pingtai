from __future__ import annotations

import json
import unittest

from agent_platform.extensions.manifest import ExtensionManifest
from agent_platform.extensions.oci_runtime import (
    SandboxedExtensionRuntime,
    SignedExtensionPackage,
)
from agent_platform.sandbox.oci import SandboxResult


class _Resolver:
    def __init__(self, package):
        self.package = package

    async def resolve(self, manifest):
        return self.package


class _Store:
    def __init__(self):
        self.items = {}

    async def put(self, manifest, package):
        self.items[manifest.extension_id] = package

    async def get(self, manifest):
        return self.items.get(manifest.extension_id)

    async def delete(self, manifest):
        self.items.pop(manifest.extension_id, None)


class _Runner:
    def __init__(self, result=None):
        self.result = result or SandboxResult(0, b'{"result":{"ok":true}}', b"")
        self.calls = []

    async def run(self, command, *, stdin=b"", environment=None, timeout_seconds=30):
        self.calls.append((command, stdin, environment, timeout_seconds))
        return self.result


def _manifest():
    return ExtensionManifest(
        extension_id="sample-extension",
        version="1.0.0",
        platform_api="1.0.0",
        permissions=("knowledge.read",),
        entrypoint="plugin/main.py",
    )


class SandboxedExtensionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.package = SignedExtensionPackage(
            archive=b"signed-zip-bytes",
            signature=b"s" * 64,
            trusted_public_key_pem=b"trusted-pem",
        )
        self.store = _Store()
        self.runner = _Runner()
        self.audit_events = []
        self.runtime = SandboxedExtensionRuntime(
            package_resolver=_Resolver(self.package),
            artifact_store=self.store,
            sandbox_runner=self.runner,
            authorize_permission=lambda principal, manifest, permission: True,
            audit=lambda principal, manifest, action, status: self.audit_events.append(status),
            signature_verifier=lambda archive, signature, key: "a" * 64,
        )

    async def test_install_verifies_and_stores_signed_package(self):
        await self.runtime.install(_manifest())
        self.assertIs(await self.store.get(_manifest()), self.package)

    async def test_invoke_runs_isolated_python_and_records_audit(self):
        await self.runtime.install(_manifest())
        result = await self.runtime.invoke(
            _manifest(), principal=object(), action="summarize",
            payload={"text": "hello"},
        )
        self.assertEqual(result, {"ok": True})
        command, stdin, environment, timeout = self.runner.calls[0]
        self.assertEqual(command[:3], ("python", "-I", "-c"))
        self.assertIn(b'"entrypoint":"plugin/main.py"', stdin)
        self.assertIn(b'"action":"summarize"', stdin)
        self.assertIsNone(environment)
        self.assertEqual(self.audit_events, ["requested", "succeeded"])

    async def test_permission_denial_prevents_sandbox_execution(self):
        self.runtime.authorize_permission = lambda *args: False
        await self.runtime.install(_manifest())
        with self.assertRaises(PermissionError):
            await self.runtime.invoke(
                _manifest(), principal=object(), action="run", payload={},
            )
        self.assertEqual(self.runner.calls, [])

    async def test_uninstalled_extension_cannot_execute(self):
        with self.assertRaises(LookupError):
            await self.runtime.invoke(
                _manifest(), principal=object(), action="run", payload={},
            )


if __name__ == "__main__":
    unittest.main()
