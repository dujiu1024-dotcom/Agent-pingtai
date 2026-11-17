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
    MessageRole,
    SessionAssetService,
)


class SessionMessagePaginationTests(unittest.TestCase):
    def test_messages_use_stable_cursor_and_audit_reads_without_content(self):
        repository = InMemorySessionRepository()
        service = SessionAssetService(repository, InMemoryObjectStorage())
        principal = SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        class AuditWriter:
            def __init__(self):
                self.events = []

            async def record(self, **event):
                self.events.append(event)

        audit = AuditWriter()

        async def resolve_principal(request):
            return principal

        async def resolve_service(actor):
            return service

        async def authorize(actor, resource_type, resource_id, action):
            return resource_type == "session" and action in {"read", "write"}

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
            audit_writer=audit,
        ))
        client = TestClient(app)

        async def seed():
            session = await service.create_session(
                tenant_id="tenant-a", owner_id="user-a",
            )
            other_session = await service.create_session(
                tenant_id="tenant-a", owner_id="user-a",
            )
            messages = []
            for content in ("first", "second", "third"):
                messages.append(await service.append_message(
                    tenant_id="tenant-a", actor_id="user-a",
                    session_id=session.session_id, role=MessageRole.USER,
                    content=content,
                ))
            foreign = await service.append_message(
                tenant_id="tenant-a", actor_id="user-a",
                session_id=other_session.session_id, role=MessageRole.USER,
                content="foreign cursor",
            )
            return session, messages, foreign

        session, messages, foreign = asyncio.run(seed())

        first_page = client.get(
            f"/api/platform/v1/sessions/{session.session_id}/messages",
            params={"limit": 2},
        )
        self.assertEqual(first_page.status_code, 200, first_page.text)
        first_payload = first_page.json()
        self.assertEqual(
            [item["content"] for item in first_payload["items"]],
            ["second", "third"],
        )
        self.assertEqual(first_payload["next_cursor"], messages[1].message_id)

        second_page = client.get(
            f"/api/platform/v1/sessions/{session.session_id}/messages",
            params={"limit": 2, "before_message_id": first_payload["next_cursor"]},
        )
        self.assertEqual(second_page.status_code, 200, second_page.text)
        second_payload = second_page.json()
        self.assertEqual(
            [item["content"] for item in second_payload["items"]],
            ["first"],
        )
        self.assertIsNone(second_payload["next_cursor"])

        invalid_cursor = client.get(
            f"/api/platform/v1/sessions/{session.session_id}/messages",
            params={"limit": 2, "before_message_id": foreign.message_id},
        )
        self.assertEqual(invalid_cursor.status_code, 400)

        read_events = [event for event in audit.events if event["action"] == "message.list"]
        self.assertEqual(len(read_events), 2)
        self.assertEqual(read_events[0]["subject_id"], session.session_id)
        self.assertEqual(read_events[0]["detail"]["returned"], 2)
        self.assertNotIn("content", read_events[0]["detail"])
        self.assertIsNone(read_events[0]["detail"]["before_message_id"])


if __name__ == "__main__":
    unittest.main()
