from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.registry import create_registry_router
from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.identity.models import Principal


class RegistryListingApiTests(unittest.TestCase):
    def setUp(self):
        repository = InMemoryRegistryRepository()
        service = RegistryService(repository)

        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="user@example.test", display_name="User",
            )

        async def resolve_service(actor):
            return service

        async def authorize(actor, kind, item_id, action):
            return action in {"read", "write", "publish"} and kind in {
                "agent", "workflow", "tool", "skill", "model_provider", "model_template",
            }

        app = FastAPI()
        app.include_router(create_registry_router(
            resolve_principal=principal, resolve_service=resolve_service,
            authorize=authorize,
        ))
        self.client = TestClient(app)

    def test_list_returns_latest_version_and_is_tenant_scoped(self):
        created = self.client.post(
            "/api/platform/v1/registry/agent/agent-a/versions",
            json={"definition": {"name": "Research Agent", "role": "research"}},
        )
        self.assertEqual(created.status_code, 201)

        listing = self.client.get("/api/platform/v1/registry/agent")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()["items"]), 1)
        self.assertEqual(listing.json()["items"][0]["item_id"], "agent-a")
        self.assertEqual(listing.json()["items"][0]["version"], 1)

        other_tenant = self.client.get(
            "/api/platform/v1/registry/agent", headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(other_tenant.json()["items"], [])

    def test_invalid_limit_is_rejected(self):
        response = self.client.get(
            "/api/platform/v1/registry/agent", params={"limit": 0},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
