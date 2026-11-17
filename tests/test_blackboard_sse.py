from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.blackboard import create_blackboard_router
from agent_platform.identity.models import Principal
from agent_platform.blackboard.replay import BlackboardReplayCursor
from agent_platform.blackboard.sse import (
    decode_blackboard_cursor,
    encode_blackboard_cursor,
    stream_blackboard_events,
)
from agent_platform.contracts import BlackboardEvent, Scope


class FakeReplay:
    tenant_id = "tenant-a"

    def __init__(self):
        self.timestamp = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
        self.base = BlackboardReplayCursor(self.timestamp, str(uuid4()))
        self.event = BlackboardEvent(
            event_type="blackboard.entry_revised",
            scope=Scope.TASK,
            scope_id="task-a",
            entry_id=str(uuid4()),
            entry_version=2,
            actor_id="user-a",
            payload={"content": {"secret": "not streamed"}},
            occurred_at=self.timestamp + timedelta(seconds=1),
        )
        self.last_cursor = None
        self.delivered = False

    async def latest_cursor(self, *, scope, scope_id):
        assert scope is Scope.TASK and scope_id == "task-a"
        return self.base

    async def list_after(self, *, cursor, scope, scope_id, limit):
        self.last_cursor = cursor
        if not self.delivered:
            self.delivered = True
            return [self.event]
        return []

    @staticmethod
    def cursor_for(event):
        return BlackboardReplayCursor(event.occurred_at, event.event_id)


class BlackboardSseBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_emits_durable_change_with_reconnect_cursor_and_minimal_payload(self):
        replay = FakeReplay()
        stream = stream_blackboard_events(
            replay=replay, scope=Scope.TASK, scope_id="task-a", poll_interval=0.001,
        )
        event = await anext(stream)
        self.assertEqual(event.event, "blackboard_update")
        self.assertEqual(event.data["event_type"], "blackboard.entry_revised")
        self.assertEqual(event.data["entry_version"], 2)
        self.assertNotIn("payload", event.data)
        cursor = decode_blackboard_cursor(event.event_id)
        self.assertEqual(cursor.event_id, replay.event.event_id)
        self.assertEqual(cursor.occurred_at, replay.event.occurred_at)
        await stream.aclose()

    async def test_cursor_round_trip_and_invalid_header_rejection(self):
        cursor = BlackboardReplayCursor(
            datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc), str(uuid4()),
        )
        self.assertEqual(decode_blackboard_cursor(encode_blackboard_cursor(cursor)), cursor)
        for invalid in ("", "missing-separator", "not-a-time|bad-id"):
            with self.assertRaises(ValueError):
                decode_blackboard_cursor(invalid)

    async def test_respects_existing_reconnect_cursor(self):
        replay = FakeReplay()
        stream = stream_blackboard_events(
            replay=replay, scope=Scope.TASK, scope_id="task-a",
            cursor=replay.base, poll_interval=0.001,
        )
        await anext(stream)
        self.assertEqual(replay.last_cursor, replay.base)
        await stream.aclose()


class BlackboardSseApiTests(unittest.TestCase):
    def _client(self, *, replay_tenant="tenant-a", allowed=True):
        replay = FakeReplay()
        replay.tenant_id = replay_tenant

        async def resolve_principal(request):
            return Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="session-a",
                email="user@example.com", display_name="User",
            )

        async def resolve_repository(principal):
            return type("Repository", (), {"tenant_id": "tenant-a"})()

        async def authorize_scope(principal, scope, scope_id, action):
            return allowed and scope is Scope.TASK and scope_id == "task-a" and action == "read"

        async def resolve_event_replay(principal):
            return replay

        app = FastAPI()
        app.include_router(create_blackboard_router(
            resolve_principal=resolve_principal,
            resolve_repository=resolve_repository,
            authorize_scope=authorize_scope,
            resolve_event_replay=resolve_event_replay,
        ))
        return TestClient(app), replay

    def test_authorized_scope_receives_blackboard_sse_event(self):
        client, replay = self._client()
        with client.stream(
            "GET",
            "/api/platform/v1/blackboard/events/stream?scope=task&scope_id=task-a",
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            lines = iter(response.iter_lines())
            event_line = next(line for line in lines if line.startswith("event:"))
            data_line = next(line for line in lines if line.startswith("data:"))
            self.assertEqual(event_line, "event: blackboard_update")
            payload = json.loads(data_line.removeprefix("data: "))
            self.assertEqual(payload["scope_id"], "task-a")
            self.assertEqual(payload["entry_version"], 2)
            self.assertNotIn("payload", payload)
        self.assertTrue(replay.delivered)

    def test_scope_authorization_and_replay_tenant_are_enforced(self):
        denied_client, _ = self._client(allowed=False)
        denied = denied_client.get(
            "/api/platform/v1/blackboard/events/stream?scope=task&scope_id=task-a",
        )
        self.assertEqual(denied.status_code, 403)

        mismatch_client, _ = self._client(replay_tenant="tenant-b")
        mismatch = mismatch_client.get(
            "/api/platform/v1/blackboard/events/stream?scope=task&scope_id=task-a",
        )
        self.assertEqual(mismatch.status_code, 403)


if __name__ == "__main__":
    unittest.main()
