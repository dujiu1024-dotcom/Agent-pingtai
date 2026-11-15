from __future__ import annotations

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.registry import create_registry_router
from agent_platform.api.sessions import create_sessions_router
from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage, InMemorySessionRepository, SessionAssetService,
)
from agent_platform.identity.models import Principal


class ControlPlaneApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry_repositories = {}
        self.session_repositories = {}
        self.storage = InMemoryObjectStorage()

        async def principal(request):
            return Principal(
                user_id=request.headers.get("x-user", "user-a"),
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session",
                email="user@example.test",
                display_name="Test User",
            )

        async def registry_service(p):
            repo = self.registry_repositories.setdefault(p.tenant_id, InMemoryRegistryRepository())
            return RegistryService(repo)

        async def session_service(p):
            repo = self.session_repositories.setdefault(p.tenant_id, InMemorySessionRepository())
            return SessionAssetService(repo, self.storage)

        async def authorize(p, resource_type, resource_id, action):
            return True

        app = FastAPI()
        app.include_router(create_registry_router(
            resolve_principal=principal, resolve_service=registry_service, authorize=authorize,
        ))
        app.include_router(create_sessions_router(
            resolve_principal=principal, resolve_service=session_service, authorize=authorize,
        ))
        self.client = TestClient(app)

    def test_registry_publish_and_tenant_isolation(self):
        created = self.client.post(
            "/api/platform/v1/registry/agent/researcher/versions",
            json={"definition": {"model_template": {"id": "base", "version": 1}}},
        )
        self.assertEqual(created.status_code, 201)
        draft_version = created.json()["version"]
        published = self.client.post(
            f"/api/platform/v1/registry/agent/researcher/versions/{draft_version}/publish",
        )
        self.assertEqual(published.status_code, 200)
        active = self.client.get("/api/platform/v1/registry/agent/researcher/active")
        self.assertEqual(active.status_code, 200)
        isolated = self.client.get(
            "/api/platform/v1/registry/agent/researcher/active",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(isolated.status_code, 404)

    def test_session_message_and_file_upload_download(self):
        created = self.client.post("/api/platform/v1/sessions", json={"title": "Test"})
        self.assertEqual(created.status_code, 201)
        session_id = created.json()["session_id"]
        message = self.client.post(
            f"/api/platform/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": "Analyze this", "run_id": "run-1"},
        )
        self.assertEqual(message.status_code, 201)
        listed = self.client.get(f"/api/platform/v1/sessions/{session_id}/messages")
        self.assertEqual(listed.json()["items"][0]["run_id"], "run-1")
        uploaded = self.client.post(
            "/api/platform/v1/files",
            params={"session_id": session_id},
            content=b"sample file",
            headers={"x-filename": "sample.txt", "content-type": "text/plain"},
        )
        self.assertEqual(uploaded.status_code, 201)
        asset_id = uploaded.json()["asset_id"]
        downloaded = self.client.get(f"/api/platform/v1/files/{asset_id}")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"sample file")


if __name__ == "__main__":
    unittest.main()
