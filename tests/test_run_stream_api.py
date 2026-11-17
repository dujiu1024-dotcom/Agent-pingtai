from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.runs import create_runs_router
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager
from agent_platform.streaming.run_events import RunEventBroker


class RunEventStreamingApiTests(unittest.TestCase):
    def test_authorized_run_stream_replays_events_and_ends_at_completion(self):
        store = InMemoryRunStateStore()
        lifecycle = RunLifecycleManager(store)
        broker = RunEventBroker()

        async def resolve_principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve_lifecycle(_principal):
            return lifecycle

        async def authorize(_principal, resource, resource_id, action):
            return resource == "run" and resource_id == "run-a" and action == "read"

        async def resolve_broker(_principal):
            return broker

        import asyncio
        asyncio.run(store.create("tenant-a", "run-a"))
        asyncio.run(broker.publish(
            tenant_id="tenant-a", run_id="run-a", event_type="RUN_START",
        ))
        asyncio.run(broker.publish(
            tenant_id="tenant-a", run_id="run-a", event_type="TOKEN_STREAM",
            data={"token": "live"},
        ))
        asyncio.run(broker.publish(
            tenant_id="tenant-a", run_id="run-a", event_type="COMPLETE",
            data={"status": "succeeded"},
        ))

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=resolve_principal,
            resolve_lifecycle=resolve_lifecycle,
            authorize=authorize,
            resolve_event_broker=resolve_broker,
        ))
        response = TestClient(app).get("/api/platform/v1/runs/run-a/stream")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: token_stream", response.text)
        self.assertIn('"token":"live"', response.text)
        self.assertIn("event: run_complete", response.text)

    def test_run_stream_requires_authorization(self):
        store = InMemoryRunStateStore()
        lifecycle = RunLifecycleManager(store)

        async def resolve_principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve_lifecycle(_principal):
            return lifecycle

        async def authorize(_principal, _resource, _resource_id, _action):
            return False

        async def resolve_broker(_principal):
            return RunEventBroker()

        import asyncio
        asyncio.run(store.create("tenant-a", "run-a"))
        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=resolve_principal,
            resolve_lifecycle=resolve_lifecycle,
            authorize=authorize,
            resolve_event_broker=resolve_broker,
        ))
        response = TestClient(app).get("/api/platform/v1/runs/run-a/stream")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
