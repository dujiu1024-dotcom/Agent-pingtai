from __future__ import annotations

from pathlib import Path
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.prompts import create_prompt_registry_router
from agent_platform.identity.models import Principal
from agent_platform.observability.prompts import PromptRegistry, tenant_prompt_key


class AsyncPromptRegistry:
    def __init__(self):
        self.inner = PromptRegistry()

    async def register(self, prompt_id, template, *, created_by):
        return self.inner.register(prompt_id, template)

    async def list_versions(self, prompt_id):
        return self.inner.list_versions(prompt_id)

    async def resolve(self, prompt_id, *, version):
        return self.inner.resolve(prompt_id, version=version)

    async def create_variant(self, prompt_id, name, version, *, created_by):
        return self.inner.create_variant(prompt_id, name, version, created_by=created_by)

    async def list_variants(self, prompt_id):
        return self.inner.list_variants(prompt_id)

    async def assign_variant(self, prompt_id, name, version, *, updated_by):
        return self.inner.assign_variant(prompt_id, name, version, updated_by=updated_by)

    async def deprecate_variant(self, prompt_id, name, *, updated_by):
        return self.inner.deprecate_variant(prompt_id, name, updated_by=updated_by)

    async def variant_history(self, prompt_id, name):
        return self.inner.variant_history(prompt_id, name)

    async def activate(self, prompt_id, version, *, updated_by, evaluation_run_id=None):
        self.inner.activate(prompt_id, version)

    async def rollback(self, prompt_id, version, *, updated_by, evaluation_run_id=None):
        self.inner.activate(prompt_id, version)

    async def set_canary(self, prompt_id, version, percentage, *, updated_by, evaluation_run_id=None):
        self.inner.set_canary(prompt_id, version, percentage)

    async def list_audit(self, prompt_id, *, limit=100):
        return []


class PromptManagementApiTests(unittest.TestCase):
    def setUp(self):
        self.registry = AsyncPromptRegistry()
        self.authorized = []
        self.app = FastAPI()
        self.app.include_router(create_prompt_registry_router(
            registry=self.registry,
            authorize=lambda actor, prompt_id, action: self.authorized.append(
                (actor.tenant_id, prompt_id, action)
            ) or True,
            evaluate_gate=lambda **kwargs: {"gate_passed": True, "eval_run_id": "eval-1"},
        ))

        @self.app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("operator-a", "tenant-a", "session-a", "a@example.test", "Operator"),
            )
            return await call_next(request)

        self.client = TestClient(self.app)

    def test_version_history_detail_and_diff_are_tenant_scoped(self):
        first = self.client.post(
            "/api/platform/v1/prompts/support/versions",
            json={"template": "Hello {name}"},
        )
        second = self.client.post(
            "/api/platform/v1/prompts/support/versions",
            json={"template": "Hello there {name}"},
        )
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        versions = self.client.get("/api/platform/v1/prompts/support/versions")
        self.assertEqual([item["version"] for item in versions.json()["items"]], [1, 2])
        detail = self.client.get("/api/platform/v1/prompts/support/versions/2")
        self.assertEqual(detail.json()["template"], "Hello there {name}")
        diff = self.client.get(
            "/api/platform/v1/prompts/support/versions/2/diff?base_version=1"
        )
        self.assertEqual(diff.status_code, 200)
        self.assertIn("+Hello there {name}", diff.json()["diff"])
        expected_key = tenant_prompt_key("tenant-a", "support")
        self.assertTrue(all(prompt_id == "support" for _, prompt_id, _ in self.authorized))
        self.assertEqual(set(self.registry.inner._versions), {expected_key})

    def test_variant_create_assign_history_and_deprecate(self):
        self.client.post(
            "/api/platform/v1/prompts/support/versions",
            json={"template": "v1"},
        )
        self.client.post(
            "/api/platform/v1/prompts/support/versions",
            json={"template": "v2"},
        )
        created = self.client.post(
            "/api/platform/v1/prompts/support/variants",
            json={"name": "candidate", "version": 1},
        )
        self.assertEqual(created.status_code, 201)
        assigned = self.client.post(
            "/api/platform/v1/prompts/support/variants/candidate/versions",
            json={"version": 2},
        )
        self.assertEqual(assigned.json()["current_version"], 2)
        history = self.client.get(
            "/api/platform/v1/prompts/support/variants/candidate/history"
        )
        self.assertEqual(history.json()["items"], [1, 2])
        deprecated = self.client.post(
            "/api/platform/v1/prompts/support/variants/candidate/deprecate",
            json={},
        )
        self.assertEqual(deprecated.json()["status"], "deprecated")
        variants = self.client.get("/api/platform/v1/prompts/support/variants")
        self.assertEqual(variants.json()["items"][0]["status"], "deprecated")

    def test_read_access_is_enforced_for_version_listing(self):
        app = FastAPI()
        app.include_router(create_prompt_registry_router(
            registry=self.registry, authorize=lambda actor, prompt_id, action: False,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("operator-a", "tenant-a", "session-a", "a@example.test", "Operator"),
            )
            return await call_next(request)

        response = TestClient(app).get("/api/platform/v1/prompts/support/versions")
        self.assertEqual(response.status_code, 403)


def test_prompt_lab_is_integrated_into_existing_workbench_shell():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert 'data-view="prompt-lab"' in html
    assert '"prompt-lab":["prompt_management"]' in script
    for route in (
        'API+"/prompts/"', "/versions", "/variants", "/audit?limit=50",
    ):
        assert route in script
    assert "X-CSRF-Token" in script
    assert "credentials:\"same-origin\"" in script


if __name__ == "__main__":
    unittest.main()
