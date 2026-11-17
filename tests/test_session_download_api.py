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


class PresignStorage(InMemoryObjectStorage):
    async def presign_get(self, object_key: str, *, expires_in: int = 300) -> str:
        if object_key not in self.objects:
            raise KeyError(object_key)
        return f"https://storage.example.test/{expires_in}/signed"


class SessionFileDownloadAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemorySessionRepository()
        self.storage = PresignStorage()
        self.service = SessionAssetService(self.repository, self.storage)
        self.principal = SimpleNamespace(tenant_id="tenant-a", user_id="user-a")
        self.denied: set[tuple[str, str, str]] = set()

        async def resolve_principal(_request):
            return self.principal

        async def resolve_service(_principal):
            return self.service

        async def authorize(_principal, resource_type, resource_id, action):
            return (resource_type, resource_id, action) not in self.denied

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
        ))
        self.client = TestClient(app)

        async def seed():
            session = await self.service.create_session(
                tenant_id="tenant-a", owner_id="user-a", project_id="project-a",
            )
            asset = await self.service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="report.pdf",
                media_type="application/pdf", content=b"%PDF-test",
                project_id="project-a", session_id=session.session_id,
            )
            return session, asset

        self.session, self.asset = asyncio.run(seed())

    def test_download_url_and_streamed_download_require_file_session_and_project_access(self):
        url_path = f"/api/platform/v1/files/{self.asset.asset_id}/download-url"
        download_path = f"/api/platform/v1/files/{self.asset.asset_id}"

        response = self.client.get(url_path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["asset_id"], self.asset.asset_id)
        self.assertEqual(response.json()["expires_in"], 300)
        self.assertTrue(response.json()["url"].startswith("https://"))
        self.assertEqual(response.headers["cache-control"], "no-store")

        downloaded = self.client.get(download_path)
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.content, b"%PDF-test")
        self.assertIn("attachment;", downloaded.headers["content-disposition"])

        # File ACL is checked independently of linked session/project grants.
        self.denied.add(("file", self.asset.asset_id, "read"))
        denied_url = self.client.get(url_path)
        denied_download = self.client.get(download_path)
        self.assertEqual(denied_url.status_code, 403)
        self.assertEqual(denied_download.status_code, 403)
        self.assertNotIn("url", denied_url.json())
        self.assertNotEqual(denied_download.content, b"%PDF-test")
        self.denied.clear()

        self.denied.add(("session", self.session.session_id, "read"))
        self.assertEqual(self.client.get(url_path).status_code, 403)
        self.assertEqual(self.client.get(download_path).status_code, 403)
        self.denied.clear()

        self.denied.add(("project", "project-a", "read"))
        self.assertEqual(self.client.get(url_path).status_code, 403)
        self.assertEqual(self.client.get(download_path).status_code, 403)

    def test_unavailable_asset_never_receives_a_download_url(self):
        async def reject():
            await self.repository.set_asset_status(
                "tenant-a", self.asset.asset_id, AssetStatus.REJECTED,
            )

        asyncio.run(reject())
        response = self.client.get(
            f"/api/platform/v1/files/{self.asset.asset_id}/download-url",
        )
        self.assertEqual(response.status_code, 404)



    def test_upload_to_project_session_requires_project_file_create(self):
        self.denied.add(("project", "project-a", "file.create"))
        denied = self.client.post(
            f"/api/platform/v1/files?session_id={self.session.session_id}",
            content=b"private project file",
            headers={
                "content-type": "text/plain",
                "x-filename": "private.txt",
            },
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        assets_after_denial = asyncio.run(
            self.repository.list_assets("tenant-a", "user-a"),
        )
        self.assertEqual(len(assets_after_denial), 1)
        self.assertEqual(assets_after_denial[0].asset_id, self.asset.asset_id)

        self.denied.clear()
        allowed = self.client.post(
            f"/api/platform/v1/files?session_id={self.session.session_id}",
            content=b"authorized project file",
            headers={
                "content-type": "text/plain",
                "x-filename": "allowed.txt",
            },
        )
        self.assertEqual(allowed.status_code, 201, allowed.text)
        self.assertEqual(allowed.json()["status"], "available")
        self.assertNotEqual(allowed.json()["asset_id"], self.asset.asset_id)
        self.assertEqual(allowed.json()["filename"], "allowed.txt")

if __name__ == "__main__":
    unittest.main()
