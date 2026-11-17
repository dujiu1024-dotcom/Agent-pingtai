from __future__ import annotations

import unittest
from types import SimpleNamespace


class SessionManagementApiTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from agent_platform.api.sessions import create_sessions_router
            from agent_platform.control_plane.sessions_assets import (
                AssetStatus, FileAsset, InMemoryObjectStorage,
                InMemorySessionRepository, MessageRole, SessionAssetService,
            )
        except ImportError:
            self.skipTest("FastAPI is installed in the platform API test environment")

        self.repository = InMemorySessionRepository()
        self.service = SessionAssetService(self.repository, InMemoryObjectStorage())
        self.principal = SimpleNamespace(tenant_id="tenant-a", user_id="user-a")
        self.denied_actions: set[str] = set()

        async def resolve_principal(_request):
            return self.principal

        async def resolve_service(_principal):
            return self.service

        async def authorize(_principal, resource_type, _resource_id, action):
            return action not in self.denied_actions

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
        ))
        self.client = TestClient(app)
        self.MessageRole = MessageRole

    def _create_session(self):
        import asyncio
        return asyncio.run(self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", title="API lifecycle",
        ))

    def test_detail_export_archive_restore_and_delete(self):
        import asyncio
        session = self._create_session()
        asyncio.run(self.repository.create_asset(FileAsset(
            tenant_id="tenant-a", asset_id="asset-1", owner_id="user-a",
            object_key="tenant-a/asset-1/v1", filename="brief.txt",
            media_type="text/plain", size_bytes=12, sha256="abc123",
            status=AssetStatus.AVAILABLE, session_id=session.session_id,
        )))
        asyncio.run(self.service.append_message(
            tenant_id="tenant-a", actor_id="user-a", session_id=session.session_id,
            role=self.MessageRole.USER, content="preserve this conversation",
            asset_ids=("asset-1",),
        ))

        detail = self.client.get(f"/api/platform/v1/sessions/{session.session_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["messages"][0]["content"], "preserve this conversation")

        exported = self.client.get(
            f"/api/platform/v1/sessions/{session.session_id}/export?format=json",
        )
        self.assertEqual(exported.status_code, 200)
        self.assertIn("attachment;", exported.headers["content-disposition"])
        self.assertEqual(exported.json()["messages"][0]["content"], "preserve this conversation")
        self.assertEqual(exported.json()["assets"][0]["asset_id"], "asset-1")
        self.assertEqual(exported.json()["assets"][0]["versions"][0]["version"], 1)

        markdown = self.client.get(
            f"/api/platform/v1/sessions/{session.session_id}/export?format=markdown",
        )
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("preserve this conversation", markdown.text)

        archived = self.client.post(
            f"/api/platform/v1/sessions/{session.session_id}/archive",
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json()["status"], "archived")
        blocked_write = self.client.post(
            f"/api/platform/v1/sessions/{session.session_id}/messages",
            json={"role": "user", "content": "must not append while archived"},
        )
        self.assertEqual(blocked_write.status_code, 409)
        self.assertEqual(self.client.get("/api/platform/v1/sessions").json()["items"], [])
        self.assertEqual(
            len(self.client.get("/api/platform/v1/sessions?include_archived=true").json()["items"]),
            1,
        )

        restored = self.client.post(
            f"/api/platform/v1/sessions/{session.session_id}/restore",
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["status"], "active")

        deleted = self.client.delete(f"/api/platform/v1/sessions/{session.session_id}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(
            self.client.get(f"/api/platform/v1/sessions/{session.session_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/api/platform/v1/sessions/{session.session_id}/restore",
            ).status_code,
            404,
        )

    def test_session_delete_propagates_to_linked_assets_and_blocks_file_routes(self):
        import asyncio
        session = self._create_session()
        asset = asyncio.run(self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="session-private.txt",
            media_type="text/plain", content=b"private session attachment",
            session_id=session.session_id,
        ))
        asyncio.run(self.service.append_message(
            tenant_id="tenant-a", actor_id="user-a", session_id=session.session_id,
            role=self.MessageRole.USER, content="attached evidence",
            asset_ids=(asset.asset_id,),
        ))

        deleted = self.client.delete(f"/api/platform/v1/sessions/{session.session_id}")
        self.assertEqual(deleted.status_code, 204)
        saved_asset = asyncio.run(self.repository.get_asset("tenant-a", asset.asset_id))
        self.assertEqual(saved_asset.status.value, "deleted")
        self.assertNotIn(asset.object_key, self.service.storage.objects)
        self.assertEqual(
            self.client.get(f"/api/platform/v1/files/{asset.asset_id}/metadata").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/platform/v1/files/{asset.asset_id}/download-url").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/platform/v1/sessions/{session.session_id}/messages",
            ).status_code,
            404,
        )

    def test_session_permissions_and_tenant_isolation_fail_closed(self):
        import asyncio
        session = self._create_session()
        asset = asyncio.run(self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="tenant-private.txt",
            media_type="text/plain", content=b"tenant-a only",
            session_id=session.session_id,
        ))
        self.denied_actions.add("read")
        self.assertEqual(
            self.client.get(f"/api/platform/v1/sessions/{session.session_id}").status_code,
            403,
        )
        self.denied_actions.clear()
        self.principal = SimpleNamespace(tenant_id="tenant-b", user_id="user-b")
        self.assertEqual(
            self.client.get(f"/api/platform/v1/sessions/{session.session_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(f"/api/platform/v1/sessions/{session.session_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/platform/v1/files/{asset.asset_id}/metadata").status_code,
            404,
        )
        remaining = asyncio.run(self.repository.get_asset("tenant-a", asset.asset_id))
        self.assertEqual(remaining.status.value, "available")
        self.assertIn(asset.object_key, self.service.storage.objects)
