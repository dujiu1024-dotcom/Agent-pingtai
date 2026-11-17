from __future__ import annotations

import unittest

from agent_platform.extensions.manifest import ExtensionManifest
from agent_platform.extensions.oci_runtime import (
    _BOOTSTRAP,
    SandboxedExtensionRuntime,
    SignedExtensionPackage,
    TrustedPublisherKey,
)
from agent_platform.sandbox.oci import SandboxResult


class _Resolver:
    def __init__(self, package):
        self.package = package

    async def resolve(self, manifest):
        return self.package


class _KeyRegistry:
    def __init__(self, key):
        self.key = key
        self.calls = []

    async def resolve(self, tenant_id, extension_id, key_id):
        self.calls.append((tenant_id, extension_id, key_id))
        if self.key is None:
            return None
        return self.key


class _Store:
    def __init__(self):
        self.items = {}

    async def put(self, tenant_id, manifest, package):
        self.items[(tenant_id, manifest.extension_id)] = package

    async def get(self, tenant_id, manifest):
        return self.items.get((tenant_id, manifest.extension_id))

    async def delete(self, tenant_id, manifest):
        self.items.pop((tenant_id, manifest.extension_id), None)


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


def _key(**overrides):
    values = {
        "publisher_id": "publisher-a",
        "key_id": "key-2026-01",
        "public_key_pem": b"-----BEGIN PUBLIC KEY-----\\ntrusted\\n-----END PUBLIC KEY-----",
        "extension_ids": frozenset({"sample-extension"}),
        "tenant_ids": frozenset({"tenant-a"}),
        "not_before": 100.0,
        "expires_at": 300.0,
        "revoked": False,
    }
    values.update(overrides)
    return TrustedPublisherKey(**values)


class SandboxedExtensionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.package = SignedExtensionPackage(
            archive=b"signed-zip-bytes",
            signature=b"s" * 64,
            publisher_key_id="key-2026-01",
        )
        self.store = _Store()
        self.runner = _Runner()
        self.audit_events = []
        self.registry = _KeyRegistry(_key())
        self.runtime = SandboxedExtensionRuntime(
            tenant_id="tenant-a",
            package_resolver=_Resolver(self.package),
            publisher_key_resolver=self.registry,
            artifact_store=self.store,
            sandbox_runner=self.runner,
            authorize_permission=lambda principal, manifest, permission: True,
            audit=lambda principal, manifest, action, status: self.audit_events.append(status),
            signature_verifier=lambda archive, signature, key: "a" * 64,
            clock=lambda: 200.0,
        )

    async def test_embedded_sandbox_bootstrap_is_valid_python(self):
        compile(_BOOTSTRAP, "<extension-bootstrap>", "exec")

    async def test_install_verifies_against_registry_key_and_stores_package(self):
        await self.runtime.install(_manifest())
        self.assertIs(await self.store.get("tenant-a", _manifest()), self.package)
        self.assertEqual(self.registry.calls, [("tenant-a", "sample-extension", "key-2026-01")])

    async def test_invoke_re_resolves_key_and_runs_isolated_python(self):
        await self.runtime.install(_manifest())
        result = await self.runtime.invoke(
            _manifest(),
            principal=type("Principal", (), {"tenant_id": "tenant-a"})(),
            action="summarize",
            payload={"text": "hello"},
        )
        self.assertEqual(result, {"ok": True})
        command, stdin, environment, timeout = self.runner.calls[0]
        self.assertEqual(command[:3], ("python", "-I", "-c"))
        self.assertIn(b'"entrypoint":"plugin/main.py"', stdin)
        self.assertIn(b'"action":"summarize"', stdin)
        self.assertIsNone(environment)
        self.assertEqual(self.audit_events, ["requested", "succeeded"])
        self.assertEqual(len(self.registry.calls), 2)

    async def test_unknown_publisher_key_fails_closed(self):
        self.registry.key = None
        with self.assertRaises(PermissionError):
            await self.runtime.install(_manifest())
        self.assertEqual(self.store.items, {})

    async def test_key_must_be_bound_to_extension(self):
        self.registry.key = _key(extension_ids=frozenset({"another-extension"}))
        with self.assertRaises(PermissionError):
            await self.runtime.install(_manifest())

    async def test_expired_key_is_rejected(self):
        self.registry.key = _key(expires_at=199.0)
        with self.assertRaises(PermissionError):
            await self.runtime.install(_manifest())

    async def test_not_yet_valid_key_is_rejected(self):
        self.registry.key = _key(not_before=201.0)
        with self.assertRaises(PermissionError):
            await self.runtime.install(_manifest())

    async def test_revoked_key_is_rejected(self):
        self.registry.key = _key(revoked=True)
        with self.assertRaises(PermissionError):
            await self.runtime.install(_manifest())

    async def test_cross_tenant_key_binding_is_rejected(self):
        self.registry.key = _key(tenant_ids=frozenset({"tenant-b"}))
        with self.assertRaises(PermissionError):
            await self.runtime.install(_manifest())

    async def test_revocation_after_install_blocks_invocation(self):
        await self.runtime.install(_manifest())
        self.registry.key = _key(revoked=True)
        with self.assertRaises(PermissionError):
            await self.runtime.invoke(
                _manifest(),
                principal=type("Principal", (), {"tenant_id": "tenant-a"})(),
                action="run",
                payload={},
            )
        self.assertEqual(self.runner.calls, [])

    async def test_permission_denial_prevents_sandbox_execution(self):
        self.runtime.authorize_permission = lambda *args: False
        await self.runtime.install(_manifest())
        with self.assertRaises(PermissionError):
            await self.runtime.invoke(
                _manifest(),
                principal=type("Principal", (), {"tenant_id": "tenant-a"})(),
                action="run",
                payload={},
            )
        self.assertEqual(self.runner.calls, [])

    async def test_cross_tenant_invocation_is_rejected_before_store_access(self):
        with self.assertRaises(PermissionError):
            await self.runtime.invoke(
                _manifest(),
                principal=type("Principal", (), {"tenant_id": "tenant-b"})(),
                action="run",
                payload={},
            )
        self.assertEqual(self.runner.calls, [])

    async def test_uninstalled_extension_cannot_execute(self):
        with self.assertRaises(LookupError):
            await self.runtime.invoke(
                _manifest(),
                principal=type("Principal", (), {"tenant_id": "tenant-a"})(),
                action="run",
                payload={},
            )


if __name__ == "__main__":
    unittest.main()
