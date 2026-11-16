from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.sessions import create_sessions_router
from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage,
    InMemorySessionRepository,
    SessionAssetService,
)


class FileDeleteApiAuthorizationTests(unittest.TestCase):
    def _client(self, *, denied: set[tuple[str, str, str]] | None = None):
        repository = InMemorySessionRepository()
        storage = InMemoryObjectStorage()
        service = SessionAssetService(repository, storage)
        principal = SimpleNamespace(tenant_id="tenant-a", user_id="user-a")
        denied = denied if denied is not None else set()

        async def resolve_principal(_request):
            return principal

        async def resolve_service(_principal):
            return service

        async def authorize(_principal, resource_type, resource_id, action):
            return (resource_type, resource_id, action) not in denied

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
        ))
        return TestClient(app), service, repository, storage

    def test_delete_requires_file_and_linked_resource_permissions(self):
        denied = {("project", "project-a", "file.delete")}
        client, service, repository, storage = self._client(denied=denied)

        async def seed():
            session = await service.create_session(
                tenant_id="tenant-a", owner_id="user-a", project_id="project-a",
            )
            asset = await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="source.txt",
                media_type="text/plain", content=b"secret", session_id=session.session_id,
            )
            return session, asset

        session, asset = asyncio.run(seed())
        response = client.delete(f"/api/platform/v1/files/{asset.asset_id}")
        self.assertEqual(response.status_code, 403)
        self.assertIn(asset.object_key, storage.objects)
        persisted = asyncio.run(repository.get_asset("tenant-a", asset.asset_id))
        self.assertEqual(persisted.status.value, "available")

        denied.clear()
        response = client.delete(f"/api/platform/v1/files/{asset.asset_id}")
        self.assertEqual(response.status_code, 204)
        persisted = asyncio.run(repository.get_asset("tenant-a", asset.asset_id))
        self.assertEqual(persisted.status.value, "deleted")
        self.assertNotIn(asset.object_key, storage.objects)

    def test_delete_requires_linked_session_write_permission(self):
        client, service, _repository, storage = self._client(
            denied={("session", "session-a", "write")},
        )

        async def seed():
            session = await service.create_session(
                tenant_id="tenant-a", owner_id="user-a",
            )
            # Use the actual session ID in the authorizer's deny set.
            asset = await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="source.txt",
                media_type="text/plain", content=b"secret", session_id=session.session_id,
            )
            return session, asset

        session, asset = asyncio.run(seed())
        # Rebuild the client with the concrete linked session permission denied.
        client, _service, _repository, storage = self._client(
            denied={("session", session.session_id, "write")},
        )
        response = client.delete(f"/api/platform/v1/files/{asset.asset_id}")
        self.assertEqual(response.status_code, 403)
        self.assertIn(asset.object_key, storage.objects)


if __name__ == "__main__":
    unittest.main()
