from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.connectors import create_connectors_router
from agent_platform.api.sessions import create_sessions_router
from agent_platform.control_plane.data_connectors import (
    ConnectorError,
    ConnectorPage,
    DataConnection,
    DataConnectorService,
    InMemoryConnectorRepository,
)
from agent_platform.control_plane.legacy_conversation_adapter import (
    LegacyConversationAdapter,
    LegacyMappingError,
)
from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    InMemoryObjectStorage,
    InMemorySessionRepository,
    MessageRole,
    SessionAssetService,
    SessionError,
)
from agent_platform.knowledge.ingestion import ParserRegistry


class _PresignStorage(InMemoryObjectStorage):
    def presign_get(self, object_key: str, *, expires_in: int):
        if object_key not in self.objects:
            raise KeyError(object_key)
        return f"https://storage.example.test/{object_key}?ttl={expires_in}"


class _FailingQueue:
    async def enqueue(self, *, tenant_id: str, asset_id: str):
        raise RuntimeError("queue unavailable")


class _ConnectorProvider:
    def __init__(self, page=None, *, error=None, before_return=None):
        self.page = page or ConnectorPage((), None, False)
        self.error = error
        self.before_return = before_return
        self.revoked = False
        self.calls = 0

    async def fetch_page(self, *, credential, scopes, cursor):
        self.calls += 1
        if self.before_return is not None:
            await self.before_return()
        if self.error is not None:
            raise self.error
        return self.page

    async def revoke(self, *, credential):
        self.revoked = True


class _Secrets:
    async def resolve(self, secret_ref):
        return "runtime-token"


class _AuditWriter:
    def __init__(self):
        self.events = []

    async def record(self, **event):
        self.events.append(event)


class _Sink:
    def __init__(self):
        self.upserts = []
        self.revocations = []

    async def upsert_page(self, *, tenant_id, connection_id, items):
        self.upserts.append((tenant_id, connection_id, items))

    async def revoke_connection(self, *, tenant_id, connection_id, disposition):
        self.revocations.append((tenant_id, connection_id, disposition))


class SessionAndMessageContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_message_roles_trace_metadata_and_inactive_boundary(self):
        repository = InMemorySessionRepository()
        service = SessionAssetService(repository, InMemoryObjectStorage())
        session = await service.create_session(
            tenant_id="tenant-a", owner_id="user-a", title="x" * 240,
        )
        self.assertEqual(len(session.title), 200)

        saved = []
        for role in MessageRole:
            saved.append(await service.append_message(
                tenant_id="tenant-a", actor_id=f"actor-{role.value}",
                session_id=session.session_id, role=role,
                content=f"{role.value} message", run_id="run-1", task_id="task-1",
                metadata={"source": "contract-test"},
            ))
        rows = await repository.list_messages("tenant-a", session.session_id)
        self.assertEqual([row.role for row in rows], list(MessageRole))
        self.assertEqual(rows[0].run_id, "run-1")
        self.assertEqual(rows[0].task_id, "task-1")
        self.assertEqual(rows[0].metadata["source"], "contract-test")

        repository.sessions[("tenant-a", session.session_id)] = replace(
            session, status="archived",
        )
        with self.assertRaises(SessionError):
            await service.append_message(
                tenant_id="tenant-a", actor_id="user-a",
                session_id=session.session_id, role=MessageRole.USER, content="late write",
            )
        with self.assertRaises(SessionError):
            await service.append_message(
                tenant_id="tenant-a", actor_id=" ", session_id=session.session_id,
                role=MessageRole.USER, content="missing actor",
            )

    async def test_message_list_limit_rejects_zero_negative_and_overflow(self):
        repository = InMemorySessionRepository()
        service = SessionAssetService(repository, InMemoryObjectStorage())
        session = await service.create_session(tenant_id="tenant-a", owner_id="user-a")
        for invalid in (0, -1, 501):
            with self.assertRaisesRegex(ValueError, "limit must be between 1 and 500"):
                await repository.list_messages("tenant-a", session.session_id, invalid)
        self.assertEqual(await repository.list_messages("tenant-a", session.session_id, 1), ())


class FileAssetContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_exact_size_sanitizes_name_and_persists_digest(self):
        repository = InMemorySessionRepository()
        storage = InMemoryObjectStorage()
        service = SessionAssetService(
            repository, storage, max_upload_bytes=4,
            allowed_media_types=frozenset({"text/plain"}),
        )
        asset = await service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a",
            filename="../../report final.txt", media_type="text/plain", content=b"1234",
        )
        self.assertEqual(asset.size_bytes, 4)
        self.assertEqual(asset.filename, "report_final.txt")
        self.assertEqual(asset.sha256, hashlib.sha256(b"1234").hexdigest())
        self.assertEqual((await repository.get_asset("tenant-a", asset.asset_id)).sha256, asset.sha256)
        with self.assertRaises(SessionError):
            await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a",
                filename="empty.txt", media_type="text/plain", content=b"",
            )
        with self.assertRaises(SessionError):
            await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a",
                filename="image.png", media_type="image/png", content=b"x",
            )

    async def test_upload_rejects_malformed_media_types_and_normalizes_valid_values(self):
        repository = InMemorySessionRepository()
        storage = InMemoryObjectStorage()
        service = SessionAssetService(repository, storage)
        for invalid in ("", "text", "text/plain\nX-Injected: yes", " text/plain", "text/plain "):
            with self.subTest(media_type=invalid), self.assertRaisesRegex(SessionError, "invalid media type"):
                await service.upload_asset(
                    tenant_id="tenant-a", owner_id="user-a", filename="bad.txt",
                    media_type=invalid, content=b"body",
                )
        asset = await service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="good.txt",
            media_type="Text/Plain", content=b"body",
        )
        self.assertEqual(asset.media_type, "text/plain")

    async def test_new_version_rejects_malformed_media_type_without_changing_current(self):
        repository = InMemorySessionRepository()
        storage = InMemoryObjectStorage()
        service = SessionAssetService(repository, storage)
        asset = await service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="base.txt",
            media_type="text/plain", content=b"base",
        )
        for invalid in ("text", "text/plain\r\nX: injected"):
            with self.subTest(media_type=invalid), self.assertRaisesRegex(SessionError, "invalid media type"):
                await service.create_asset_version(
                    tenant_id="tenant-a", asset_id=asset.asset_id,
                    content=b"replacement", media_type=invalid, expected_version=1,
                )
        current = await repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(current.version, 1)
        self.assertEqual(current.sha256, asset.sha256)
        self.assertEqual(storage.objects, {asset.object_key: b"base"})

    async def test_corrupt_storage_and_invalid_download_ttl_fail_closed(self):
        repository = InMemorySessionRepository()
        storage = _PresignStorage()
        service = SessionAssetService(repository, storage)
        asset = await service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a",
            filename="report.txt", media_type="text/plain", content=b"original",
        )
        for ttl in (0, -1, 901):
            with self.assertRaisesRegex(SessionError, "TTL"):
                await service.create_download_url(
                    tenant_id="tenant-a", asset_id=asset.asset_id, expires_in=ttl,
                )
        storage.objects[asset.object_key] = b"tampered"
        with self.assertRaisesRegex(SessionError, "integrity"):
            await service.download_asset(tenant_id="tenant-a", asset_id=asset.asset_id)

    async def test_processing_enqueue_failure_rejects_asset_and_removes_object(self):
        repository = InMemorySessionRepository()
        storage = InMemoryObjectStorage()
        service = SessionAssetService(
            repository, storage, processing_queue=_FailingQueue(),
        )
        with self.assertRaisesRegex(SessionError, "could not be scheduled"):
            await service.upload_asset(
                tenant_id="tenant-a", owner_id="user-a",
                filename="queued.txt", media_type="text/plain", content=b"body",
            )
        self.assertEqual(len(repository.assets), 1)
        saved = next(iter(repository.assets.values()))
        self.assertEqual(saved.status, AssetStatus.REJECTED)
        self.assertEqual(storage.objects, {})


class FileParserContractTests(unittest.TestCase):
    def test_registry_rejects_unregistered_media_type(self):
        with self.assertRaisesRegex(ValueError, "no parser registered"):
            ParserRegistry().parse(b"opaque", "application/x-unknown")


class ConnectorContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryConnectorRepository()
        self.provider = _ConnectorProvider()
        self.sink = _Sink()
        self.service = DataConnectorService(
            repository=self.repository, providers={"drive": self.provider},
            secrets=_Secrets(), sink=self.sink,
        )
        await self.repository.create(DataConnection(
            tenant_id="tenant-a", connection_id="drive-1", provider="drive",
            secret_ref="secret://tenant-a/drive-1", scopes=("files.read",),
        ), created_by="user-a")

    async def test_registration_rejects_unknown_provider_and_invalid_contract_fields(self):
        base = {
            "tenant_id": "tenant-a", "connection_id": "drive-2", "provider": "drive",
            "display_name": "Drive", "secret_ref": "secret://tenant-a/drive-2",
            "scopes": ("files.read",), "actor_id": "user-a",
        }
        for changes in (
            {"provider": "unknown"},
            {"connection_id": "../escape"},
            {"display_name": " "},
            {"secret_ref": "plaintext"},
            {"scopes": ()},
        ):
            with self.subTest(changes=changes), self.assertRaises(ConnectorError):
                await self.service.create_connection(**(base | changes))

    async def test_provider_failure_is_recorded_without_advancing_cursor(self):
        self.provider.error = TimeoutError("provider timed out")
        with self.assertRaisesRegex(ConnectorError, "synchronization failed"):
            await self.service.sync_once(tenant_id="tenant-a", connection_id="drive-1")
        saved = await self.repository.get("tenant-a", "drive-1")
        self.assertIsNone(saved.cursor)
        self.assertEqual(saved.version, 0)
        self.assertIn("TimeoutError", saved.last_error)
        self.assertEqual(self.sink.upserts, [])

    async def test_revocation_during_fetch_prevents_page_commit(self):
        async def revoke_mid_sync():
            await self.repository.revoke(tenant_id="tenant-a", connection_id="drive-1")

        self.provider.before_return = revoke_mid_sync
        self.provider.page = ConnectorPage(({"id": "doc-1"},), {"next": "2"}, True)
        with self.assertRaisesRegex(ConnectorError, "(?:changed|revoked) during synchronization"):
            await self.service.sync_once(tenant_id="tenant-a", connection_id="drive-1")
        saved = await self.repository.get("tenant-a", "drive-1")
        self.assertEqual(saved.status, "revoked")
        self.assertEqual(saved.version, 1)
        self.assertIsNone(saved.cursor)
        self.assertEqual(self.sink.upserts, [])
        self.assertEqual(self.sink.revocations, [("tenant-a", "drive-1", "retain")])


class ConnectorProjectApiContractTests(unittest.TestCase):
    def test_project_scope_is_required_for_connector_create_and_revoke(self):
        repository = InMemoryConnectorRepository()
        provider = _ConnectorProvider()
        sink = _Sink()
        denied_projects = set()
        audit = _AuditWriter()

        async def resolve_principal(_request):
            from agent_platform.identity.models import Principal
            return Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="session-a",
                email="user@example.test", display_name="User",
            )

        async def resolve_service(_principal):
            return DataConnectorService(
                repository=repository, providers={"drive": provider},
                secrets=_Secrets(), sink=sink,
            )

        async def authorize(_principal, resource_type, resource_id, action):
            return not (resource_type == "project" and resource_id in denied_projects)

        app = FastAPI()
        app.include_router(create_connectors_router(
            resolve_principal=resolve_principal, resolve_service=resolve_service,
            authorize=authorize, audit_writer=audit,
        ))
        client = TestClient(app)
        payload = {
            "connection_id": "drive-project", "provider": "drive",
            "display_name": "Project Drive",
            "secret_ref": "secret://tenant-a/project-drive",
            "scopes": ["files.read"], "project_id": "project-a",
        }
        denied_projects.add("project-a")
        denied_create = client.post("/api/platform/v1/connectors", json=payload)
        self.assertEqual(denied_create.status_code, 403)
        self.assertIsNone(asyncio_run(repository.get("tenant-a", "drive-project")))

        denied_projects.clear()
        created = client.post("/api/platform/v1/connectors", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        denied_projects.add("project-a")
        revoked = client.post("/api/platform/v1/connectors/drive-project/revoke")
        self.assertEqual(revoked.status_code, 403)
        self.assertEqual(asyncio_run(repository.get("tenant-a", "drive-project")).status, "active")
        denied_projects.clear()
        successful_revoke = client.post("/api/platform/v1/connectors/drive-project/revoke")
        self.assertEqual(successful_revoke.status_code, 200)
        self.assertEqual([event["action"] for event in audit.events], ["connector.created", "connector.revoked"])
        self.assertNotIn("secret_ref", str(audit.events))


def asyncio_run(awaitable):
    import asyncio
    return asyncio.run(awaitable)


class ResourceAuditContractTests(unittest.TestCase):
    def test_session_and_message_actions_emit_actor_tenant_and_safe_metadata(self):
        repository = InMemorySessionRepository()
        service = SessionAssetService(repository, InMemoryObjectStorage())
        audit = _AuditWriter()

        async def resolve_principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve_service(_principal):
            return service

        async def authorize(_principal, _resource_type, _resource_id, _action):
            return True

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal, resolve_service=resolve_service,
            authorize=authorize, audit_writer=audit,
        ))
        client = TestClient(app)
        created = client.post("/api/platform/v1/sessions", json={"title": "Audit me"})
        self.assertEqual(created.status_code, 201, created.text)
        session_id = created.json()["session_id"]
        appended = client.post(
            f"/api/platform/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": "hello"},
        )
        self.assertEqual(appended.status_code, 201, appended.text)
        actions = [event["action"] for event in audit.events]
        self.assertIn("session.created", actions)
        self.assertIn("resource.access_granted", actions)
        self.assertIn("message.appended", actions)
        for event in audit.events:
            self.assertEqual(event["tenant_id"], "tenant-a")
            self.assertEqual(event["actor_id"], "user-a")
        self.assertNotIn("hello", str(audit.events))

class LegacyAdapterContractTests(unittest.TestCase):
    def test_legacy_mapping_requires_explicit_ownership_and_project_approval(self):
        adapter = LegacyConversationAdapter()
        row = {"conversation_id": "legacy-1", "owner_id": "user-a", "project_id": "old-project"}
        with self.assertRaises(LegacyMappingError):
            adapter.adapt_session(row, tenant_id="tenant-a")
        with self.assertRaises(LegacyMappingError):
            adapter.adapt_session(
                {"conversation_id": "legacy-2"}, tenant_id="tenant-a",
                approved_project_id="project-a",
            )
        mapped = adapter.adapt_session(
            row, tenant_id="tenant-a", approved_project_id="project-a",
        )
        self.assertEqual(mapped.project_id, "project-a")
        with self.assertRaises(LegacyMappingError):
            adapter.adapt_message(
                {"message_id": "legacy-msg", "role": "user", "content": "hi"},
                tenant_id="tenant-a", session_id="", actor_id="user-a",
            )


if __name__ == "__main__":
    unittest.main()
