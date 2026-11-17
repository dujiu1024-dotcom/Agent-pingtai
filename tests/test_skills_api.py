from __future__ import annotations

import unittest
from types import SimpleNamespace


class SkillApiTests(unittest.TestCase):
    def test_skill_lifecycle_routes_are_tenant_and_agent_scoped(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from agent_platform.api.skills import create_skill_router
        except ImportError:
            self.skipTest("FastAPI is installed in the platform API test environment")

        class Repository:
            tenant_id = "tenant-a"
            async def list_items(self, tenant, kind, limit=100):
                return [SimpleNamespace(status="published", item_id="summarizer", version=3,
                    definition={"manifest": {"name": "summarizer", "version": "1.2.0",
                                             "description": "Summarize reports"}})]
            async def get_active(self, tenant, kind, skill):
                return SimpleNamespace(item_id=skill, version=3,
                    definition={"manifest": {"name": skill, "version": "1.2.0",
                                             "description": "Summarize reports"}})

        class Service:
            def __init__(self):
                self.registry = SimpleNamespace(repository=Repository())
                self.agent_store = SimpleNamespace(get=self.get)
                self.calls = []
            async def get(self, tenant, agent, skill):
                if not self.calls:
                    return None
                version = self.calls[-1][1].get("version", 3)
                return SimpleNamespace(skill_id=skill, agent_id=agent,
                    registry_version=version, package_version="1.2.0")
            async def install_for_agent(self, **kwargs):
                self.calls.append(("install", kwargs))
                return SimpleNamespace(skill_id=kwargs["skill_id"], agent_id=kwargs["agent_id"],
                    registry_version=kwargs["version"], package_version="1.2.0")
            async def uninstall_from_agent(self, **kwargs):
                self.calls.append(("uninstall", kwargs))

        service = Service()
        async def principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")
        async def resolve(_principal):
            return service
        async def authorize(_principal, _agent, _action):
            return True

        app = FastAPI()
        app.include_router(create_skill_router(resolve_principal=principal,
            resolve_service=resolve, authorize=authorize))
        client = TestClient(app)
        listed = client.get("/api/platform/v1/skills?agent_id=agent-1")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["skill_id"], "summarizer")
        installed = client.post("/api/platform/v1/skills/summarizer/install",
            json={"agent_id": "agent-1", "version": 3})
        self.assertEqual(installed.status_code, 201)
        upgraded = client.post("/api/platform/v1/skills/summarizer/upgrade",
            json={"agent_id": "agent-1", "version": 4})
        self.assertEqual(upgraded.status_code, 200)
        removed = client.delete("/api/platform/v1/skills/summarizer/install?agent_id=agent-1")
        self.assertEqual(removed.status_code, 200)
        invalid = client.post("/api/platform/v1/skills/summarizer/install",
            json={"agent_id": "agent-1", "version": True})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual([call[0] for call in service.calls], ["install", "install", "uninstall"])


if __name__ == "__main__":
    unittest.main()
