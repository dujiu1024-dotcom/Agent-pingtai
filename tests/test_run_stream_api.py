from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.runs import create_runs_router
from agent_platform.streaming.sse import SSEEvent


class _Store:
    async def get(self, tenant_id, run_id):
        if (tenant_id, run_id) == ("tenant-a", "run-a"):
            return SimpleNamespace(run_id=run_id)
        return None


class _Broker:
    async def subscribe(self, *, tenant_id, run_id, last_event_id=None):
        assert tenant_id == "tenant-a"
        assert run_id == "run-a"
        yield SSEEvent(
            event="token_stream", event_id="run-a:1",
            data={"run_id": run_id, "sequence": 1, "event_type": "TOKEN_STREAM", "token": "live"},
        )
        yield SSEEvent(
            event="run_complete", event_id="run-a:2",
            data={"run_id": run_id, "sequence": 2, "event_type": "COMPLETE"},
        )


class RunEventStreamingApiTests(unittest.TestCase):
    def _app(self, allowed):
        lifecycle = SimpleNamespace(store=_Store())

        async def resolve_principal(_request):
            return SimpleNamespace(tenant_id="tenant-a", user_id="user-a")

        async def resolve_lifecycle(_principal):
            return lifecycle

        async def authorize(_principal, resource, resource_id, action):
            return allowed and resource == "run" and resource_id == "run-a" and action == "read"

        async def resolve_broker(_principal):
            return _Broker()

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=resolve_principal,
            resolve_lifecycle=resolve_lifecycle,
            authorize=authorize,
            resolve_event_broker=resolve_broker,
        ))
        return app

    def test_authorized_run_stream_replays_events_and_ends_at_completion(self):
        response = TestClient(self._app(True)).get("/api/platform/v1/runs/run-a/stream")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: token_stream", response.text)
        self.assertIn('"token":"live"', response.text)
        self.assertIn("event: run_complete", response.text)

    def test_run_stream_requires_authorization(self):
        response = TestClient(self._app(False)).get("/api/platform/v1/runs/run-a/stream")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
