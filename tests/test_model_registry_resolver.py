from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.models.registry_resolver import ModelRegistryError, RegistryModelResolver


class Secrets:
    async def resolve(self, secret_ref):
        if secret_ref == "secret://tenant-a/provider":
            return "runtime-only-api-key"
        raise ValueError("secret not found")


class ModelRegistryResolverTests(unittest.IsolatedAsyncioTestCase):
    def snapshot(self, *, provider_version=4, base_url="https://llm.example/v1"):
        return SimpleNamespace(tenant_id="tenant-a", entries=(
            {
                "kind": "model_template", "item_id": "general", "version": 2,
                "definition": {
                    "provider_id": "openai-main", "provider_version": provider_version,
                    "model_id": "reasoning-model", "parameters": {"temperature": 0.2},
                    "capabilities": ["text"], "allowed_data_levels": ["internal"],
                },
            },
            {
                "kind": "model_provider", "item_id": "openai-main", "version": 4,
                "definition": {
                    "provider_type": "openai-compatible", "base_url": base_url,
                    "secret_ref": "secret://tenant-a/provider",
                },
            },
        ))

    async def test_resolves_model_from_run_pinned_versions(self):
        resolved = await RegistryModelResolver(Secrets()).resolve(
            snapshot=self.snapshot(), template_id="general",
        )
        self.assertEqual(resolved.template_version, 2)
        self.assertEqual(resolved.provider_version, 4)
        self.assertEqual(resolved.model_id, "reasoning-model")
        self.assertEqual(resolved.api_key, "runtime-only-api-key")
        self.assertNotIn("runtime-only-api-key", repr(resolved))
        self.assertEqual(resolved.allowed_data_levels, ("internal",))

    async def test_provider_version_mismatch_is_rejected(self):
        with self.assertRaises(ModelRegistryError):
            await RegistryModelResolver(Secrets()).resolve(
                snapshot=self.snapshot(provider_version=3), template_id="general",
            )

    async def test_loopback_http_requires_explicit_development_opt_in(self):
        with self.assertRaises(ModelRegistryError):
            await RegistryModelResolver(Secrets()).resolve(
                snapshot=self.snapshot(base_url="http://localhost:8080/v1"),
                template_id="general",
            )
        resolved = await RegistryModelResolver(
            Secrets(), allow_insecure_loopback=True,
        ).resolve(
            snapshot=self.snapshot(base_url="http://localhost:8080/v1"),
            template_id="general",
        )
        self.assertEqual(resolved.base_url, "http://localhost:8080/v1")

    async def test_secret_reference_must_be_scoped_to_snapshot_tenant(self):
        snapshot = self.snapshot()
        snapshot.entries[1]["definition"]["secret_ref"] = "secret://tenant-b/provider"
        with self.assertRaises(ModelRegistryError):
            await RegistryModelResolver(Secrets()).resolve(
                snapshot=snapshot, template_id="general",
            )

    async def test_secret_reference_path_traversal_is_rejected(self):
        snapshot = self.snapshot()
        snapshot.entries[1]["definition"]["secret_ref"] = "secret://tenant-a/../other"
        with self.assertRaises(ModelRegistryError):
            await RegistryModelResolver(Secrets()).resolve(
                snapshot=snapshot, template_id="general",
            )

    async def test_non_https_remote_provider_is_rejected(self):
        with self.assertRaises(ModelRegistryError):
            await RegistryModelResolver(Secrets()).resolve(
                snapshot=self.snapshot(base_url="http://llm.example/v1"),
                template_id="general",
            )


if __name__ == "__main__":
    unittest.main()
