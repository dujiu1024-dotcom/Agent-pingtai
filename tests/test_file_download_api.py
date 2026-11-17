from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.sessions import create_sessions_router
from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    InMemoryObjectStorage,
    InMemorySessionRepository,
    SessionAssetService,
)


class TrackingObjectStorage(InMemoryObjectStorage):
    def __init__(self):
        super().__init__()
        self.get_calls = []
        self.presign_calls = []

    async def get(self, object_key):
        self.get_calls.append(object_key)
        return await super().get(object_key)

    def presign_get(self, object_key, *, expires_in):
        self.presign_calls.append((object_key, expires_in))
        return f"https://storage.example.test/{object_key}?ttl={expires_in}"


class FileDownloadApiAuthorizationTests(unittest.TestCase):
    def _client(self, *, denied=None):
        repository = InMemorySessionRepository()
        storage = TrackingObjectStorage()
        service = SessionAssetService(repository, storage)
        denied = denied if denied is not None else set()

        async def resolve_principal(request):
            return SimpleNamespace(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id="user-a",
            )

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
        return TestClient(app), service, repository, storage, denied

    @staticmethod
    def _seed(service):
        async def seed():
            session = await service.create_session(
                tenant_id="tenant-a", owner_id="user-a", project_id="project-a",
            )
            asset = await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="private.txt",
                media_type="text/plain", content=b"private file contents",
                session_id=session.session_id,
            )
            return session, asset
        return asyncio.run(seed())

    def test_download_requires_file_session_and_project_read_permissions(self):
        client, service, _repository, storage, denied = self._client()
        session, asset = self._seed(service)

        for permission in (
            ("file", asset.asset_id, "read"),
            ("session", session.session_id, "read"),
            ("project", "project-a", "read"),
        ):
            denied.add(permission)
            response = client.get(f"/api/platform/v1/files/{asset.asset_id}")
            self.assertEqual(response.status_code, 403, permission)
            self.assertEqual(storage.get_calls, [])
            denied.remove(permission)

        response = client.get(f"/api/platform/v1/files/{asset.asset_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"private file contents")
        self.assertEqual(len(storage.get_calls), 1)

    def test_download_url_requires_file_session_and_project_read_permissions(self):
        client, service, _repository, storage, denied = self._client()
        session, asset = self._seed(service)

        for permission in (
            ("file", asset.asset_id, "read"),
            ("session", session.session_id, "read"),
            ("project", "project-a", "read"),
        ):
            denied.add(permission)
            response = client.get(f"/api/platform/v1/files/{asset.asset_id}/download-url")
            self.assertEqual(response.status_code, 403, permission)
            self.assertEqual(storage.presign_calls, [])
            denied.remove(permission)

        response = client.get(
            f"/api/platform/v1/files/{asset.asset_id}/download-url?expires_in=120",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["expires_in"], 120)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(storage.presign_calls, [(asset.object_key, 120)])

    def test_file_resource_is_not_visible_across_tenants(self):
        client, service, _repository, storage, _denied = self._client()
        _session, asset = self._seed(service)

        response = client.get(
            f"/api/platform/v1/files/{asset.asset_id}",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(storage.get_calls, [])

        signed = client.get(
            f"/api/platform/v1/files/{asset.asset_id}/download-url",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(signed.status_code, 404)
        self.assertEqual(storage.presign_calls, [])

    def test_unavailable_file_cannot_be_downloaded_or_presigned(self):
        client, service, repository, storage, _denied = self._client()
        _session, asset = self._seed(service)
        asyncio.run(repository.set_asset_status(
            "tenant-a", asset.asset_id,
            AssetStatus.REJECTED,
        ))
        response = client.get(f"/api/platform/v1/files/{asset.asset_id}")
        self.assertEqual(response.status_code, 404)
        response = client.get(f"/api/platform/v1/files/{asset.asset_id}/download-url")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(storage.get_calls, [])
        self.assertEqual(storage.presign_calls, [])


    def test_version_api_creates_lists_and_downloads_historical_content(self):
        client, _service, _repository, _storage, _denied = self._client()
        _session, asset = self._seed(_service)

        created = client.post(
            f"/api/platform/v1/files/{asset.asset_id}/versions?expected_version=1",
            content=b"revised file bytes",
            headers={"x-filename": "revised.txt", "content-type": "text/plain"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["version"], 2)

        history = client.get(f"/api/platform/v1/files/{asset.asset_id}/versions")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["current_version"], 2)
        self.assertEqual([item["version"] for item in history.json()["items"]], [1, 2])

        old = client.get(f"/api/platform/v1/files/{asset.asset_id}/versions/1")
        self.assertEqual(old.status_code, 200)
        self.assertEqual(old.content, b"private file contents")
        self.assertEqual(old.headers["x-file-asset-version"], "1")

        current = client.get(f"/api/platform/v1/files/{asset.asset_id}")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.content, b"revised file bytes")

        restored = client.post(
            f"/api/platform/v1/files/{asset.asset_id}/versions/1/restore?expected_version=2",
        )
        self.assertEqual(restored.status_code, 201, restored.text)
        self.assertEqual(restored.json()["version"], 3)
        self.assertEqual(restored.json()["restored_from_version"], 1)
        current = client.get(f"/api/platform/v1/files/{asset.asset_id}")
        self.assertEqual(current.content, b"private file contents")

    def test_version_api_enforces_file_write_and_read_permissions(self):
        client, service, _repository, _storage, denied = self._client()
        _session, asset = self._seed(service)
        denied.add(("file", asset.asset_id, "write"))
        response = client.post(
            f"/api/platform/v1/files/{asset.asset_id}/versions",
            content=b"not allowed",
        )
        self.assertEqual(response.status_code, 403)
        denied.remove(("file", asset.asset_id, "write"))

        denied.add(("file", asset.asset_id, "read"))
        response = client.get(f"/api/platform/v1/files/{asset.asset_id}/versions")
        self.assertEqual(response.status_code, 403)
        response = client.get(f"/api/platform/v1/files/{asset.asset_id}/versions/1")
        self.assertEqual(response.status_code, 403)

if __name__ == "__main__":
    unittest.main()
