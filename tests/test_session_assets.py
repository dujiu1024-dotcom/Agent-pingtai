from __future__ import annotations

import unittest

from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    InMemoryObjectStorage,
    InMemorySessionRepository,
    MessageRole,
    SessionAssetService,
    SessionError,
    SessionNotFound,
)


class RejectScanner:
    async def scan(self, filename: str, media_type: str, content: bytes) -> bool:
        return False


class SessionAssetServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemorySessionRepository()
        self.storage = InMemoryObjectStorage()
        self.service = SessionAssetService(self.repository, self.storage)

    async def test_session_messages_and_run_trace(self) -> None:
        session = await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", title="Planning",
        )
        message = await self.service.append_message(
            tenant_id="tenant-a", actor_id="user-a", session_id=session.session_id,
            role=MessageRole.USER, content="Please analyze this", run_id="run-1", task_id="task-1",
        )
        self.assertEqual(message.run_id, "run-1")
        self.assertEqual((await self.repository.list_messages("tenant-a", session.session_id))[0], message)

    async def test_cross_tenant_session_and_asset_are_not_visible(self) -> None:
        session = await self.service.create_session(tenant_id="tenant-a", owner_id="user-a")
        with self.assertRaises(SessionNotFound):
            await self.service.append_message(
                tenant_id="tenant-b", actor_id="user-b", session_id=session.session_id,
                role=MessageRole.USER, content="cross tenant",
            )

    async def test_upload_download_integrity_and_session_link(self) -> None:
        session = await self.service.create_session(tenant_id="tenant-a", owner_id="user-a")
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="../../report.pdf",
            media_type="application/pdf", content=b"%PDF-sample",
            session_id=session.session_id,
        )
        self.assertEqual(asset.status, AssetStatus.AVAILABLE)
        saved, content = await self.service.download_asset(tenant_id="tenant-a", asset_id=asset.asset_id)
        self.assertEqual(saved.sha256, asset.sha256)
        self.assertEqual(content, b"%PDF-sample")

    async def test_file_inherits_session_project_and_rejects_mismatch(self) -> None:
        session = await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", project_id="project-a",
        )
        inherited = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="a.txt",
            media_type="text/plain", content=b"a", session_id=session.session_id,
        )
        self.assertEqual(inherited.project_id, "project-a")
        with self.assertRaises(SessionError):
            await self.service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="b.txt",
                media_type="text/plain", content=b"b", session_id=session.session_id,
                project_id="project-b",
            )

    async def test_upload_size_and_scanner_rejection(self) -> None:
        limited = SessionAssetService(self.repository, self.storage, max_upload_bytes=3)
        with self.assertRaises(SessionError):
            await limited.upload_asset(
                tenant_id="tenant-a", owner_id="user-a", filename="x.txt",
                media_type="text/plain", content=b"1234",
            )
        scanned = SessionAssetService(self.repository, self.storage, scanner=RejectScanner())
        asset = await scanned.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="x.txt",
            media_type="text/plain", content=b"ok",
        )
        self.assertEqual(asset.status, AssetStatus.REJECTED)
        with self.assertRaises(SessionNotFound):
            await scanned.download_asset(tenant_id="tenant-a", asset_id=asset.asset_id)


if __name__ == "__main__":
    unittest.main()
