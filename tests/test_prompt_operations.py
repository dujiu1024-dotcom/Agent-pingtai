from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.prompt_operations import create_prompt_operations_router
from agent_platform.identity.models import Principal
from agent_platform.observability.prompts import PromptRegistry


class AuditRecorder:
    def __init__(self):
        self.events = []
        self.fail = False

    async def record(self, **event):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


class GateEvidence:
    def __init__(self, passed=True):
        self.passed = passed
        self.evidence_id = "gate-evidence-1"


class AsyncRegistry:
    def __init__(self):
        self.inner = PromptRegistry()

    def register(self, prompt_id, template):
        return self.inner.register(prompt_id, template)

    async def resolve(self, prompt_id, *, version=None, rollout_key=""):
        return self.inner.resolve(prompt_id, version=version, rollout_key=rollout_key)

    async def render(self, prompt_id, variables, *, version=None, rollout_key=""):
        return self.inner.render(prompt_id, variables, version=version, rollout_key=rollout_key)

    async def activate(self, prompt_id, version, *, updated_by):
        self.inner.activate(prompt_id, version)

    async def set_canary(self, prompt_id, version, percentage, *, updated_by):
        self.inner.set_canary(prompt_id, version, percentage)


class PromptOperationsTests(unittest.TestCase):
    def setUp(self):
        self.registry = AsyncRegistry()
        self.key = "tenant:8:tenant-a:assistant"
        self.first = self.registry.register(self.key, "Answer {question}")
        self.second = self.registry.register(self.key, "Carefully answer {question}")
        self.registry.activate(self.key, self.first.version)
        self.audit = AuditRecorder()
        self.gate = GateEvidence()
        self.app = FastAPI()
        self.app.include_router(create_prompt_operations_router(
            registry=self.registry,
            authorize=lambda actor, prompt_id, action: True,
            evaluate_candidate=lambda tenant, prompt, version: self.gate,
            audit_writer=self.audit,
        ))

        @self.app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("user-1", "tenant-a", "session-1", "user@example.com", "User"),
            )
            return await call_next(request)

        self.client = TestClient(self.app)

    def test_explicit_version_test_audits_only_metadata(self):
        response = self.client.post("/api/platform/v1/prompts/assistant/test", json={
            "version": self.second.version, "variables": {"question": "secret question"},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rendered"], "Carefully answer secret question")
        self.assertEqual(self.audit.events[0]["action"], "prompt.version.tested")
        self.assertNotIn("secret question", str(self.audit.events[0]["detail"]))

    def test_release_requires_passing_evaluation_evidence(self):
        self.gate = GateEvidence(False)
        rejected = self.client.post("/api/platform/v1/prompts/assistant/release", json={
            "version": self.second.version, "canary_percentage": 10,
        })
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.registry.resolve(self.key).version, self.first.version)

        self.gate = GateEvidence(True)
        accepted = self.client.post("/api/platform/v1/prompts/assistant/release", json={
            "version": self.second.version, "canary_percentage": 10,
        })
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["evaluation_evidence_id"], "gate-evidence-1")
        self.assertEqual(self.audit.events[-1]["detail"]["canary_percentage"], 10.0)

    def test_audit_failure_prevents_release_mutation(self):
        self.audit.fail = True
        response = self.client.post("/api/platform/v1/prompts/assistant/release", json={
            "version": self.second.version, "canary_percentage": 0,
        })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.registry.resolve(self.key).version, self.first.version)

    def test_rollback_restores_requested_immutable_version(self):
        response = self.client.post("/api/platform/v1/prompts/assistant/rollback", json={
            "version": self.first.version,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.registry.resolve(self.key).version, self.first.version)
        self.assertEqual(self.audit.events[-1]["action"], "prompt.version.rollback_requested")


if __name__ == "__main__":
    unittest.main()
