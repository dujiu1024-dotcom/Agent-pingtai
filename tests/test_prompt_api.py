from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.prompts import create_prompt_registry_router
from agent_platform.identity.models import Principal
from agent_platform.observability.prompts import PromptRegistry


class AsyncPromptRegistry:
    def __init__(self):
        self.inner = PromptRegistry()
        self.keys = []

    async def register(self, prompt_id, template, *, created_by):
        self.keys.append(prompt_id)
        return self.inner.register(prompt_id, template)

    async def rollback(self, prompt_id, version, *, updated_by):
        self.keys.append(prompt_id)
        self.inner.activate(prompt_id, version)

    async def list_audit(self, prompt_id, *, limit=100):
        return [{"prompt_id": prompt_id, "action": "version.rolled_back"}]

    async def activate(self, prompt_id, version, *, updated_by):
        self.keys.append(prompt_id)
        self.inner.activate(prompt_id, version)

    async def set_canary(self, prompt_id, version, percentage, *, updated_by):
        self.keys.append(prompt_id)
        self.inner.set_canary(prompt_id, version, percentage)

    async def render(self, prompt_id, variables, *, rollout_key="", version=None):
        self.keys.append(prompt_id)
        return self.inner.render(
            prompt_id, variables, rollout_key=rollout_key, version=version,
        )


class PromptRegistryApiTests(unittest.TestCase):
    def test_prompt_management_is_authenticated_authorized_and_tenant_scoped(self):
        registry = AsyncPromptRegistry()
        authorized = []
        app = FastAPI()
        app.include_router(create_prompt_registry_router(
            registry=registry,
            authorize=lambda actor, prompt_id, action: authorized.append(
                (actor.tenant_id, prompt_id, action)
            ) or True,
            evaluate_gate=lambda **kwargs: {
                "gate_passed": True, "eval_run_id": "eval-123",
            },
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("user-1", "tenant-a", "session-1", "user@example.com", "User"),
            )
            return await call_next(request)

        client = TestClient(app)
        created = client.post(
            "/api/platform/v1/prompts/support/versions",
            json={"template": "Answer {question}"},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["prompt_id"], "support")
        self.assertEqual(created.json()["version"], 1)

        second = client.post(
            "/api/platform/v1/prompts/support/versions",
            json={"template": "Respond carefully to {question}"},
        )
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["version"], 2)

        activated = client.post(
            "/api/platform/v1/prompts/support/activate", json={"version": 1},
        )
        self.assertEqual(activated.status_code, 200)
        self.assertEqual(activated.json()["eval_run_id"], "eval-123")

        canary = client.post(
            "/api/platform/v1/prompts/support/canary",
            json={"version": 2, "percentage": 10},
        )
        self.assertEqual(canary.status_code, 200)
        self.assertEqual(canary.json()["eval_run_id"], "eval-123")

        rollback = client.post(
            "/api/platform/v1/prompts/support/rollback", json={"version": 1},
        )
        self.assertEqual(rollback.status_code, 200)

        audit = client.get("/api/platform/v1/prompts/support/audit")
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.json()["items"][0]["action"], "version.rolled_back")

        rendered = client.post(
            "/api/platform/v1/prompts/support/render",
            json={"variables": {"question": "why"}, "rollout_key": "run-1"},
        )
        self.assertEqual(rendered.status_code, 200)
        self.assertEqual(rendered.json()["rendered"], "Answer why")
        self.assertTrue(all(key.startswith("tenant:8:tenant-a:") for key in registry.keys))
        self.assertEqual(
            [action for _, _, action in authorized],
            ["write", "write", "publish", "publish", "publish", "read_audit", "read"],
        )

    def test_prompt_write_requires_authorization(self):
        app = FastAPI()
        app.include_router(create_prompt_registry_router(
            registry=AsyncPromptRegistry(),
            authorize=lambda actor, prompt_id, action: False,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("user-1", "tenant-a", "session-1", "user@example.com", "User"),
            )
            return await call_next(request)

        response = TestClient(app).post(
            "/api/platform/v1/prompts/support/versions",
            json={"template": "Answer"},
        )
        self.assertEqual(response.status_code, 403)


    def test_deployment_fails_closed_without_gate_and_when_gate_rejects(self):
        for gate, expected_status in (
            (None, 503),
            (lambda **kwargs: {"gate_passed": False, "gate_failures": ["pass_rate_below_threshold"]}, 409),
        ):
            app = FastAPI()
            registry = AsyncPromptRegistry()
            app.include_router(create_prompt_registry_router(
                registry=registry,
                authorize=lambda actor, prompt_id, action: True,
                evaluate_gate=gate,
            ))

            @app.middleware("http")
            async def identity(request, call_next):
                request.state.__setattr__(
                    "identity.principal",
                    Principal("user-1", "tenant-a", "session-1", "user@example.com", "User"),
                )
                return await call_next(request)

            client = TestClient(app)
            created = client.post(
                "/api/platform/v1/prompts/support/versions",
                json={"template": "Answer {question}"},
            )
            self.assertEqual(created.status_code, 201)
            response = client.post(
                "/api/platform/v1/prompts/support/activate", json={"version": 1},
            )
            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(registry.inner._active, {})


if __name__ == "__main__":
    unittest.main()
