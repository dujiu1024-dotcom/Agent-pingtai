from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.sessions import create_sessions_router
from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage, InMemorySessionRepository, MessageRole, SessionAssetService,
)
from agent_platform.memory.extraction import MemoryDraft, MemoryExtractionService
from agent_platform.memory.service import (
    InMemoryMemoryRepository, MemoryPrincipal, MemoryScope, MemoryService,
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
        hidden_project_session = client.get("/api/platform/v1/sessions")
        self.assertEqual(
            {x["session_id"] for x in hidden_project_session.json()["items"]},
            {own.session_id},
        )
        denied["session"].update({own.session_id, project_session.session_id})
        hidden_session = client.get("/api/platform/v1/files")
        self.assertEqual(hidden_session.json()["items"], [])


    def test_session_memory_extraction_uses_one_completed_turn_and_creates_candidates(self):
        repository = InMemorySessionRepository()
        service = SessionAssetService(repository, InMemoryObjectStorage())
        principal = SimpleNamespace(tenant_id="tenant-a", user_id="user-a")
        memory_principal = MemoryPrincipal(
            tenant_id="tenant-a", user_id="user-a",
            project_ids=frozenset({"project-a"}),
            agent_ids=frozenset({"agent-a"}),
        )
        memory = MemoryService(InMemoryMemoryRepository())

        class Extractor:
            def __init__(self):
                self.turns = []

            async def extract(self, *, turn):
                self.turns.append(turn)
                return [MemoryDraft(
                    scope=MemoryScope.USER, scope_id=turn.user_id,
                    content="prefers concise technical summaries", confidence=0.9,
                )]

        extractor = Extractor()
        extraction = MemoryExtractionService(memory=memory, extractor=extractor)

        async def resolve_principal(request):
            return principal

        async def resolve_memory_principal(request):
            return memory_principal

        async def resolve_service(actor):
            return service

        async def authorize(actor, resource_type, resource_id, action):
            if resource_type == "project" and action == "write":
                return resource_id == "project-a"
            return resource_type == "session" and resource_id in {
                row.session_id for row in repository.sessions.values()
                if row.owner_id == actor.user_id
            }

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
            memory_extraction=extraction,
            resolve_memory_principal=resolve_memory_principal,
        ))
        client = TestClient(app)

        import asyncio
        async def seed():
            session = await service.create_session(
                tenant_id="tenant-a", owner_id="user-a", project_id="project-a",
            )
            await service.append_message(
                tenant_id="tenant-a", actor_id="user-a", session_id=session.session_id,
                role=MessageRole.USER, content="Please remember my reporting preference.",
            )
            await service.append_message(
                tenant_id="tenant-a", actor_id="agent-a", session_id=session.session_id,
                role=MessageRole.ASSISTANT, content="I will use concise technical summaries.",
                metadata={"agent_id": "agent-a"},
            )
            return session

        session = asyncio.run(seed())
        response = client.post(
            f"/api/platform/v1/sessions/{session.session_id}/memory-extract",
        )
        self.assertEqual(response.status_code, 202, response.text)
        payload = response.json()
        # source_ref 格式是 session://tenant/{assistant_message_id}@turn
        self.assertTrue(payload["source_ref"].startswith("session://tenant-a/"))
        self.assertIn("@1", payload["source_ref"])
        self.assertEqual(payload["items"][0]["status"], "candidate")
        self.assertEqual(len(extractor.turns), 1)
        self.assertEqual(extractor.turns[0].project_id, "project-a")
        self.assertEqual(
            extractor.turns[0].assistant_message,
            "I will use concise technical summaries.",
        )
        repeated = client.post(
            f"/api/platform/v1/sessions/{session.session_id}/memory-extract",
        )
        self.assertEqual(repeated.status_code, 202, repeated.text)
        self.assertEqual(
            repeated.json()["items"][0]["memory_id"],
            payload["items"][0]["memory_id"],
        )
        self.assertEqual(len(extractor.turns), 1)


if __name__ == "__main__":
    unittest.main()


    def test_session_memory_extraction_rejects_non_owner_even_with_session_write_access(self):
        import asyncio

        repository = InMemorySessionRepository()
        service = SessionAssetService(repository, InMemoryObjectStorage())
        principal = SimpleNamespace(tenant_id="tenant-a", user_id="reader")
        memory_principal = MemoryPrincipal(tenant_id="tenant-a", user_id="reader")
        memory = MemoryService(InMemoryMemoryRepository())

        class Extractor:
            async def extract(self, *, turn):
                raise AssertionError("extractor must not receive another user's session")

        async def resolve_principal(request):
            return principal

        async def resolve_memory_principal(request):
            return memory_principal

        async def resolve_service(actor):
            return service

        async def authorize(actor, resource_type, resource_id, action):
            return resource_type == "session" and action in {"read", "write"}

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
            memory_extraction=MemoryExtractionService(
                memory=memory, extractor=Extractor(),
            ),
            resolve_memory_principal=resolve_memory_principal,
        ))
        client = TestClient(app)

        async def seed():
            session = await service.create_session(
                tenant_id="tenant-a", owner_id="session-owner",
            )
            await service.append_message(
                tenant_id="tenant-a", actor_id="session-owner",
                session_id=session.session_id, role=MessageRole.USER,
                content="Private user preference",
            )
            await service.append_message(
                tenant_id="tenant-a", actor_id="agent-a",
                session_id=session.session_id, role=MessageRole.ASSISTANT,
                content="Private assistant response",
            )
            return session

        session = asyncio.run(seed())
        response = client.post(
            f"/api/platform/v1/sessions/{session.session_id}/memory-extract",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "memory extraction is restricted to the session owner",
        )
        self.assertEqual(asyncio.run(memory.repository.list("tenant-a")), [])
