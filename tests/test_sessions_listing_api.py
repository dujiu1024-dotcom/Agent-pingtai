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

        denied = {"session": set(), "project": set()}

        async def authorize(actor, resource_type, resource_id, action):
            if resource_id in denied.get(resource_type, set()):
                return False
            return resource_id == "*" or resource_id in {
                row.session_id for row in repository.sessions.values()
                if row.owner_id == actor.user_id
            } or resource_id in {
                row.project_id for row in repository.sessions.values()
                if row.owner_id == actor.user_id and row.project_id is not None
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
            project_session = await service.create_session(
                tenant_id="tenant-a", owner_id="user-a", project_id="project-private",
            )
            asset = await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="notes.txt",
                media_type="text/plain", content=b"notes", session_id=own.session_id,
            )
            private_asset = await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="private.txt",
                media_type="text/plain", content=b"private", session_id=project_session.session_id,
            )
            return own, asset, project_session, private_asset

        import asyncio
        own, asset, project_session, private_asset = asyncio.run(seed())
        sessions = client.get("/api/platform/v1/sessions")
        files = client.get("/api/platform/v1/files")
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(
            {x["session_id"] for x in sessions.json()["items"]},
            {own.session_id, project_session.session_id},
        )
        self.assertEqual(files.status_code, 200)
        self.assertEqual(
            {x["asset_id"] for x in files.json()["items"]},
            {asset.asset_id, private_asset.asset_id},
        )
        denied["project"].add("project-private")
        hidden = client.get("/api/platform/v1/files")
        self.assertEqual([x["asset_id"] for x in hidden.json()["items"]], [asset.asset_id])
        denied["session"].update({own.session_id, project_session.session_id})
        hidden_session = client.get("/api/platform/v1/files")
        self.assertEqual(hidden_session.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
