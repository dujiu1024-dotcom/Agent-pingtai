from __future__ import annotations
import asyncio
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_platform.api.replay import create_run_replay_router
from agent_platform.identity.models import Principal
from agent_platform.observability.run_trace import InMemoryRunTraceRepository, RunTraceEventType, RunTraceService
from agent_platform.runtime.lifecycle import InMemoryRunStateStore
from agent_platform.runtime.replay import RunReplayService

class RunReplayApiTests(unittest.TestCase):
    def setUp(self):
        self.allowed = True
        self.store = InMemoryRunStateStore()
        self.repository = InMemoryRunTraceRepository()
        self.trace = RunTraceService(repository=self.repository)
        async def resolve_context(_principal):
            class Context:
                async def rebuild(inner, *, tenant_id, run_id):
                    return {"phase":"execute","version":3,"memory_snapshot_ref":"memory:3"}
            return Context()
        async def resolve_replay(_principal):
            return self.replay
        async def principal(_request):
            return Principal(user_id="user-a",tenant_id="tenant-a",session_id="session-a",
                email="user@example.test",display_name="User")
        async def authorize(_principal, resource_type, resource_id, action):
            return self.allowed and resource_type=="run" and action in {"read","rerun"}
        asyncio.run(self.store.create("tenant-a","run-api"))
        asyncio.run(self.trace.emit(tenant_id="tenant-a",run_id="run-api",
            event_type=RunTraceEventType.STEP_START,step_id="node-a",
            payload={"replay_input":{"task":"replay"}}))
        async def rerun_step(**_kwargs): return {"ok":True}
        self.replay=RunReplayService(lifecycle_store=self.store,trace_repository=self.repository,
            resolve_context_service=resolve_context,rerun_step=rerun_step)
        app=FastAPI()
        app.include_router(create_run_replay_router(resolve_principal=principal,
            resolve_replay=resolve_replay,authorize=authorize))
        self.client=TestClient(app)

    def test_api_open_step_controls_and_rerun(self):
        opened=self.client.post("/api/platform/v1/runs/run-api/replay")
        self.assertEqual(opened.status_code,200)
        session_id=opened.json()["session_id"]
        stepped=self.client.post(f"/api/platform/v1/runs/run-api/replay/{session_id}/step")
        self.assertEqual(stepped.json()["event"]["step_id"],"node-a")
        paused=self.client.post(f"/api/platform/v1/runs/run-api/replay/{session_id}/pause")
        self.assertTrue(paused.json()["paused"])
        self.client.post(f"/api/platform/v1/runs/run-api/replay/{session_id}/resume")
        rerun=self.client.post(f"/api/platform/v1/runs/run-api/replay/{session_id}/rerun",json={"sequence":1})
        self.assertEqual(rerun.status_code,200)
        self.assertTrue(rerun.json()["result"]["ok"])

    def test_seek_and_rerun_reject_boolean_or_malformed_sequences(self):
        opened=self.client.post("/api/platform/v1/runs/run-api/replay")
        session_id=opened.json()["session_id"]
        base=f"/api/platform/v1/runs/run-api/replay/{session_id}"
        for action in ("seek", "rerun"):
            boolean=self.client.post(f"{base}/{action}",json={"sequence":True})
            self.assertEqual(boolean.status_code,400)
            malformed=self.client.post(
                f"{base}/{action}",content="{",headers={"content-type":"application/json"})
            self.assertEqual(malformed.status_code,400)

    def test_replay_routes_enforce_run_authorization(self):
        self.allowed=False
        response=self.client.post("/api/platform/v1/runs/run-api/replay")
        self.assertEqual(response.status_code,403)

if __name__=="__main__": unittest.main()
