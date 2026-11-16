from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.sessions import create_sessions_router
from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    FileAsset,
    InMemoryObjectStorage,
    InMemorySessionRepository,
    SessionAssetService,
)
from agent_platform.identity.models import Principal


class PresignedStorage(InMemoryObjectStorage):
    async def presign_get(self, object_key: str, *, expires_in: int = 300) -> str:
        return f"https://storage.example.test/{object_key}?ttl={expires_in}"


class PresignedDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_only_signs_available_tenant_asset(self):
        repository = InMemorySessionRepository()
        storage = PresignedStorage()
        service = SessionAssetService(repository, storage)
        asset = FileAsset(
            tenant_id="tenant-a", asset_id="asset-a", owner_id="user-a",
            object_key="tenant/tenant-a/assets/asset-a/hash", filename="a.txt",
            media_type="text/plain", size_bytes=1, sha256="0" * 64,
            status=AssetStatus.AVAILABLE,
        )
        await repository.create_asset(asset)
        found, url = await service.create_download_url(
            tenant_id="tenant-a", asset_id="asset-a", expires_in=120,
        )
        self.assertEqual(found.asset_id, "asset-a")
        self.assertIn("ttl=120", url)
        with self.assertRaises(Exception):
            await service.create_download_url(tenant_id="tenant-b", asset_id="asset-a")
        pending = FileAsset(
            tenant_id="tenant-a", asset_id="asset-pending", owner_id="user-a",
            object_key="tenant/tenant-a/assets/asset-pending/hash", filename="b.txt",
            media_type="text/plain", size_bytes=1, sha256="0" * 64,
            status=AssetStatus.SCANNING,
        )
        await repository.create_asset(pending)
        with self.assertRaises(Exception):
            await service.create_download_url(tenant_id="tenant-a", asset_id="asset-pending")

    async def test_api_requires_file_and_linked_project_authorization(self):
        repository = InMemorySessionRepository()
        storage = PresignedStorage()
        service = SessionAssetService(repository, storage)
        asset = FileAsset(
            tenant_id="tenant-a", asset_id="asset-a", owner_id="user-a",
            object_key="tenant/tenant-a/assets/asset-a/hash", filename="a.txt",
            media_type="text/plain", size_bytes=1, sha256="0" * 64,
            status=AssetStatus.AVAILABLE, project_id="project-a",
        )
        await repository.create_asset(asset)
        principal = Principal(
            tenant_id="tenant-a", user_id="user-a", session_id="session-a",
            email="user@example.test", display_name="User",
        )

        async def resolve_principal(_request):
            return principal

        async def resolve_service(_principal):
            return service

        denied_project = {"allowed": False}
        async def authorize(_principal, resource_type, resource_id, action):
            if resource_type == "project":
                return denied_project["allowed"]
            return resource_type == "file" and resource_id == "asset-a" and action == "read"

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
        ))
        client = TestClient(app)
        denied = client.get("/api/platform/v1/files/asset-a/download-url")
        self.assertEqual(denied.status_code, 403)
        denied_project["allowed"] = True
        allowed = client.get("/api/platform/v1/files/asset-a/download-url?expires_in=60")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["expires_in"], 60)
        self.assertNotIn("object_key", allowed.json())
