from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.sessions import create_sessions_router
from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage, InMemorySessionRepository, SessionAssetService,
)


class SessionListingApiTests(unittest.TestCase):
    def test_list_sessions_and_files_only_returns_authorized_owner_assets(self):
        repository = InMemorySessionRepository()
        storage = InMemoryObjectStorage()
        service = SessionAssetService(repository, storage)
        principal = SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve_principal(request):
            return principal

        async def resolve_service(actor):
            return service

        async def authorize(actor, resource_type, resource_id, action):
            return resource_id == "*" or resource_id in {
                row.session_id for row in repository.sessions.values()
                if row.owner_id == actor.user_id
            } or resource_id in {
                row.asset_id for row in repository.assets.values()
                if row.owner_id == actor.user_id
            }

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
        ))
        client = TestClient(app)

        async def seed():
            own = await service.create_session(
                tenant_id="tenant-a", owner_id="user-a", title="My session",
            )
            await service.create_session(
                tenant_id="tenant-a", owner_id="user-b", title="Other session",
            )
            asset = await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="notes.txt",
                media_type="text/plain", content=b"notes", session_id=own.session_id,
            )
            return own, asset

        import asyncio
        own, asset = asyncio.run(seed())
        sessions = client.get("/api/platform/v1/sessions")
        files = client.get("/api/platform/v1/files")
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual([x["session_id"] for x in sessions.json()["items"]], [own.session_id])
        self.assertEqual(files.status_code, 200)
        self.assertEqual([x["asset_id"] for x in files.json()["items"]], [asset.asset_id])


if __name__ == "__main__":
    unittest.main()
