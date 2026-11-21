from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.observability.run_trace import (
    InMemoryRunTraceRepository,
    RunTraceEventType,
    RunTraceService,
)
from agent_platform.runtime.replay import ReplayContextIncomplete, RunReplayService


class RunReplayServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryRunTraceRepository()
        self.trace = RunTraceService(repository=self.repository)
        self.lifecycle = SimpleNamespace(get=self._get_run)
        self.run_exists = True
        self.context_data = {"phase":"planning","version":7,"memory_snapshot_ref":"memory:snapshot:9",
            "objective":"ship","request":{"task":"ship"}}
        async def resolve_context(_tenant):
            class Context:
                async def rebuild(inner, *, tenant_id, run_id): return dict(self.context_data)
            return Context()
        async def memory(_tenant, ref): return {"ref":ref,"facts":["persisted"]}
        self.service = RunReplayService(lifecycle_store=self.lifecycle, trace_repository=self.repository,
            resolve_context_service=resolve_context, resolve_memory_snapshot=memory)
        await self.trace.emit(tenant_id="tenant-a",run_id="run-a",event_type=RunTraceEventType.RUN_START)
        await self.trace.emit(tenant_id="tenant-a",run_id="run-a",event_type=RunTraceEventType.STEP_START,
            step_id="step-a",payload={"replay_input":{"prompt":"persisted"}})
        await self.trace.emit(tenant_id="tenant-a",run_id="run-a",event_type=RunTraceEventType.COMPLETE)
    async def _get_run(self, tenant_id, run_id):
        if self.run_exists and tenant_id=="tenant-a" and run_id=="run-a": return SimpleNamespace(run_id=run_id)
        return None
    async def test_context_and_controls(self):
        session=await self.service.open(tenant_id="tenant-a",run_id="run-a")
        self.assertEqual(session.context["phase"],"planning")
        self.assertEqual(session.context["version"],7)
        self.assertEqual(session.context["memory_snapshot"]["facts"],["persisted"])
        timeline=await self.service.list_events(
            tenant_id="tenant-a",run_id="run-a",session_id=session.session_id,
        )
        self.assertEqual(timeline["total"],3)
        self.assertEqual(timeline["items"][1]["step_id"],"step-a")
        first=await self.service.step(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id)
        self.assertEqual(first["event"]["event_type"],"RUN_START")
        paused=await self.service.pause(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id)
        self.assertTrue(paused["paused"])
        unchanged=await self.service.step(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id)
        self.assertEqual(unchanged["cursor"],1)
        await self.service.resume(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id)
        second=await self.service.step(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id)
        self.assertEqual(second["event"]["step_id"],"step-a")
        back=await self.service.back(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id)
        self.assertEqual(back["cursor"],1)
        fast=await self.service.fast_forward(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id)
        self.assertEqual(fast["cursor"],3)
    async def test_missing_fields_fail_closed(self):
        self.context_data={"objective":"ship"}
        with self.assertRaisesRegex(ReplayContextIncomplete,"phase, version, memory_snapshot_ref"):
            await self.service.open(tenant_id="tenant-a",run_id="run-a")
    async def test_cross_tenant_run_is_not_visible(self):
        with self.assertRaisesRegex(LookupError,"Run not found"):
            await self.service.open(tenant_id="tenant-b",run_id="run-a")
    async def test_seek_and_rerun_require_persisted_input(self):
        calls=[]
        async def rerun_step(**kwargs): calls.append(kwargs); return {"replayed":True}
        self.service.rerun_step=rerun_step
        session=await self.service.open(tenant_id="tenant-a",run_id="run-a")
        result=await self.service.seek(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id,sequence=2)
        self.assertEqual(result["event"]["step_id"],"step-a")
        rerun=await self.service.rerun(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id,sequence=2)
        self.assertTrue(rerun["result"]["replayed"])
        self.assertEqual(calls[0]["context"]["version"],7)
        with self.assertRaisesRegex(ValueError,"replay_input"):
            await self.service.rerun(tenant_id="tenant-a",run_id="run-a",session_id=session.session_id,sequence=1)
    async def test_registry_snapshot_is_restored(self):
        class Registry:
            async def get(self, *, tenant_id, run_id):
                return SimpleNamespace(tenant_id=tenant_id,run_id=run_id,
                    entries=({"kind":"agent","item_id":"planner","version":4},),digest="d"*64)
        async def resolve_registry(_tenant): return Registry()
        self.service.resolve_registry_snapshot_service=resolve_registry
        session=await self.service.open(tenant_id="tenant-a",run_id="run-a")
        self.assertEqual(session.context["registry_snapshot"]["entries"][0]["version"],4)

if __name__=="__main__": unittest.main()
