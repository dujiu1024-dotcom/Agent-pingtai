from __future__ import annotations

import unittest

from agent_platform.control_plane.asset_processing import (
    AssetProcessingWorker,
    InMemoryAssetContentSink,
    InMemoryAssetProcessingQueue,
    TextAssetParser,
)
from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    InMemoryObjectStorage,
    InMemorySessionRepository,
    MessageRole,
    SessionAssetService,
    SessionError,
    SessionNotFound,
)



class AcceptScanner:
    async def scan(self, filename: str, media_type: str, content: bytes) -> bool:
        return True


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

    async def test_message_rejects_asset_from_another_project_or_session(self):
        project_a = await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", project_id="project-a",
        )
        project_b = await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", project_id="project-b",
        )
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="private.txt",
            media_type="text/plain", content=b"private", project_id="project-b",
            session_id=project_b.session_id,
        )
        with self.assertRaisesRegex(SessionError, "same project"):
            await self.service.append_message(
                tenant_id="tenant-a", actor_id="user-a", session_id=project_a.session_id,
                role=MessageRole.USER, content="attach cross-project file", asset_ids=(asset.asset_id,),
            )
        other_session = await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", project_id="project-b",
        )
        with self.assertRaisesRegex(SessionError, "another session"):
            await self.service.append_message(
                tenant_id="tenant-a", actor_id="user-a", session_id=other_session.session_id,
                role=MessageRole.USER, content="reuse already linked file", asset_ids=(asset.asset_id,),
            )

    async def test_delete_tombstones_before_storage_cleanup_and_is_retryable(self):
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="remove.txt",
            media_type="text/plain", content=b"remove me",
        )
        await self.service.delete_asset(tenant_id="tenant-a", asset_id=asset.asset_id)
        deleted = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(deleted.status, AssetStatus.DELETED)
        self.assertNotIn(asset.object_key, self.storage.objects)
        with self.assertRaises(SessionNotFound):
            await self.service.download_asset(tenant_id="tenant-a", asset_id=asset.asset_id)
        await self.service.delete_asset(tenant_id="tenant-a", asset_id=asset.asset_id)

    async def test_list_sessions_and_assets_are_owner_scoped(self):
        first = await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", title="My session",
        )
        await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-b", title="Other user's session",
        )
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="notes.txt",
            media_type="text/plain", content=b"notes", session_id=first.session_id,
        )
        sessions = await self.repository.list_sessions("tenant-a", "user-a")
        assets = await self.repository.list_assets("tenant-a", "user-a")
        self.assertEqual([item.session_id for item in sessions], [first.session_id])
        self.assertEqual([item.asset_id for item in assets], [asset.asset_id])
        self.assertEqual(await self.repository.list_sessions("tenant-b", "user-a"), ())


    async def test_asset_versions_are_immutable_and_can_be_restored(self) -> None:
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="report.txt",
            media_type="text/plain", content=b"version one",
        )
        second = await self.service.create_asset_version(
            tenant_id="tenant-a", asset_id=asset.asset_id, content=b"version two",
            expected_version=1,
        )
        self.assertEqual(second.version, 2)
        current = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(current.version, 2)
        self.assertEqual(current.sha256, second.sha256)
        history = await self.service.list_asset_versions(
            tenant_id="tenant-a", asset_id=asset.asset_id,
        )
        self.assertEqual([item.version for item in history], [1, 2])
        old, old_content = await self.service.download_asset_version(
            tenant_id="tenant-a", asset_id=asset.asset_id, version=1,
        )
        self.assertEqual(old.version, 1)
        self.assertEqual(old_content, b"version one")
        latest, latest_content = await self.service.download_asset(
            tenant_id="tenant-a", asset_id=asset.asset_id,
        )
        self.assertEqual(latest.version, 2)
        self.assertEqual(latest_content, b"version two")
        restored = await self.service.restore_asset_version(
            tenant_id="tenant-a", asset_id=asset.asset_id, version=1,
            expected_version=2,
        )
        self.assertEqual(restored.version, 3)
        restored_asset, restored_content = await self.service.download_asset(
            tenant_id="tenant-a", asset_id=asset.asset_id,
        )
        self.assertEqual(restored_asset.version, 3)
        self.assertEqual(restored_content, b"version one")

    async def test_asset_version_update_rejects_stale_writer_and_cross_tenant_reads(self) -> None:
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="report.txt",
            media_type="text/plain", content=b"initial",
        )
        await self.service.create_asset_version(
            tenant_id="tenant-a", asset_id=asset.asset_id, content=b"second",
            expected_version=1,
        )
        with self.assertRaisesRegex(SessionError, "version changed"):
            await self.service.create_asset_version(
                tenant_id="tenant-a", asset_id=asset.asset_id, content=b"stale",
                expected_version=1,
            )
        self.assertEqual(
            await self.repository.list_asset_versions("tenant-b", asset.asset_id), (),
        )
        with self.assertRaises(SessionNotFound):
            await self.service.download_asset_version(
                tenant_id="tenant-b", asset_id=asset.asset_id, version=1,
            )

    async def test_delete_removes_all_version_objects(self) -> None:
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="report.txt",
            media_type="text/plain", content=b"first",
        )
        second = await self.service.create_asset_version(
            tenant_id="tenant-a", asset_id=asset.asset_id, content=b"second",
        )
        await self.service.delete_asset(tenant_id="tenant-a", asset_id=asset.asset_id)
        self.assertNotIn(asset.object_key, self.storage.objects)
        self.assertNotIn(second.object_key, self.storage.objects)
        with self.assertRaises(SessionNotFound):
            await self.service.download_asset_version(
                tenant_id="tenant-a", asset_id=asset.asset_id, version=1,
            )


    async def test_upload_is_pending_until_background_scan_and_parse_complete(self) -> None:
        queue = InMemoryAssetProcessingQueue()
        service = SessionAssetService(
            self.repository, self.storage, processing_queue=queue,
        )
        asset = await service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="notes.txt",
            media_type="text/plain", content=b"background processing",
        )
        self.assertEqual(asset.status, AssetStatus.PENDING)
        sink = InMemoryAssetContentSink()
        worker = AssetProcessingWorker(
            queue=queue, repository=self.repository, storage=self.storage,
            scanner=AcceptScanner(), parser=TextAssetParser(), content_sink=sink,
            worker_id="asset-worker",
        )
        self.assertTrue(await worker.process_once())
        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.AVAILABLE)
        self.assertEqual(
            sink.records[("tenant-a", asset.asset_id)].text,
            "background processing",
        )

    async def test_scanner_outage_retries_and_never_exposes_unscanned_file(self) -> None:
        class RetryScanner:
            def __init__(self):
                self.calls = 0
            async def scan(self, filename, media_type, content):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("scanner offline")
                return True
        queue = InMemoryAssetProcessingQueue()
        service = SessionAssetService(
            self.repository, self.storage, processing_queue=queue,
        )
        asset = await service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="retry.txt",
            media_type="text/plain", content=b"retry safely",
        )
        scanner = RetryScanner()
        worker = AssetProcessingWorker(
            queue=queue, repository=self.repository, storage=self.storage,
            scanner=scanner, parser=TextAssetParser(),
            content_sink=InMemoryAssetContentSink(), worker_id="asset-worker",
            max_retry_delay_seconds=0,
        )
        await worker.process_once()
        pending = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(pending.status, AssetStatus.PENDING)
        with self.assertRaises(SessionNotFound):
            await service.download_asset(tenant_id="tenant-a", asset_id=asset.asset_id)
        await worker.process_once()
        available = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(available.status, AssetStatus.AVAILABLE)

    async def test_malware_detection_rejects_and_deletes_binary_object(self) -> None:
        queue = InMemoryAssetProcessingQueue()
        service = SessionAssetService(
            self.repository, self.storage, processing_queue=queue,
        )
        asset = await service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="bad.txt",
            media_type="text/plain", content=b"malware",
        )
        worker = AssetProcessingWorker(
            queue=queue, repository=self.repository, storage=self.storage,
            scanner=RejectScanner(), parser=TextAssetParser(),
            content_sink=InMemoryAssetContentSink(), worker_id="asset-worker",
        )
        await worker.process_once()
        rejected = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(rejected.status, AssetStatus.REJECTED)
        with self.assertRaises(SessionNotFound):
            await self.storage.get(asset.object_key)



    async def test_presigned_download_requires_available_asset(self) -> None:
        class SignedStorage(InMemoryObjectStorage):
            async def presign_get(self, object_key, *, expires_in=300, filename=None, media_type=None):
                return "https://objects.example.test/signed"
        storage = SignedStorage()
        service = SessionAssetService(self.repository, storage)
        asset = await service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="file.txt",
            media_type="text/plain", content=b"ready",
        )
        saved, url = await service.create_download_url(
            tenant_id="tenant-a", asset_id=asset.asset_id,
        )
        self.assertEqual(saved.asset_id, asset.asset_id)
        self.assertEqual(url, "https://objects.example.test/signed")



if __name__ == "__main__":
    unittest.main()


    async def test_list_sessions_and_assets_are_owner_scoped(self):
        first = await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", title="My session",
        )
        await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-b", title="Other user's session",
        )
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="notes.txt",
            media_type="text/plain", content=b"notes", session_id=first.session_id,
        )
        sessions = await self.repository.list_sessions("tenant-a", "user-a")
        assets = await self.repository.list_assets("tenant-a", "user-a")
        self.assertEqual([item.session_id for item in sessions], [first.session_id])
        self.assertEqual([item.asset_id for item in assets], [asset.asset_id])
        self.assertEqual(await self.repository.list_sessions("tenant-b", "user-a"), ())


if __name__ == "__main__":
    unittest.main()
