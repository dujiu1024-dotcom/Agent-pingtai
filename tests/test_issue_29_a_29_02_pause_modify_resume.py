from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.replay import create_run_replay_router
from agent_platform.identity.models import Principal
from agent_platform.observability.run_trace import (
    InMemoryRunTraceRepository,
    RunTraceEventType,
    RunTraceService,
)
from agent_platform.runtime.lifecycle import InMemoryRunStateStore
from agent_platform.runtime.replay import RunReplayService


class ReplayPauseModifyResumeAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryRunTraceRepository()
        self.trace = RunTraceService(repository=self.repository)
        self.persisted_context = {
            "phase": "execute", "version": 8, "memory_snapshot_ref": "memory:snapshot:8",
            "objective": "original objective", "request": {"goal": "original"},
            "constraints": ["tenant-safe"],
        }

        async def resolve_context(_tenant):
            class Context:
                async def rebuild(inner, *, tenant_id, run_id):
                    return dict(self.persisted_context)
            return Context()

        self.service = RunReplayService(
            lifecycle_store=SimpleNamespace(get=self._get_run),
            trace_repository=self.repository, resolve_context_service=resolve_context,
        )
        for event_type, step_id, payload in (
            (RunTraceEventType.RUN_START, None, {}),
            (RunTraceEventType.STEP_START, "step-a", {"replay_input": {"task": "original"}}),
            (RunTraceEventType.COMPLETE, None, {}),
        ):
            await self.trace.emit(
                tenant_id="tenant-a", run_id="run-a", event_type=event_type,
                step_id=step_id, payload=payload,
            )

    async def _get_run(self, tenant_id, run_id):
        return SimpleNamespace(run_id=run_id) if (tenant_id, run_id) == ("tenant-a", "run-a") else None

    async def test_pause_modify_resume_continues_from_same_cursor_with_overlay(self):
        session = await self.service.open(tenant_id="tenant-a", run_id="run-a")
        first = await self.service.step(
            tenant_id="tenant-a", run_id="run-a", session_id=session.session_id,
        )
        self.assertEqual(first["cursor"], 1)
        paused = await self.service.pause(
            tenant_id="tenant-a", run_id="run-a", session_id=session.session_id,
        )
        self.assertTrue(paused["paused"])
        patched = await self.service.update_context(
            tenant_id="tenant-a", run_id="run-a", session_id=session.session_id,
            patch={"objective": "revised objective", "request": {"goal": "revised"},
                   "constraints": ["tenant-safe", "read-only"]},
        )
        self.assertEqual(patched["cursor"], 1)
        self.assertTrue(patched["paused"])
        self.assertEqual(patched["context"]["run_context"]["objective"], "revised objective")
        resumed = await self.service.resume(
            tenant_id="tenant-a", run_id="run-a", session_id=session.session_id,
        )
        self.assertFalse(resumed["paused"])
        next_event = await self.service.step(
            tenant_id="tenant-a", run_id="run-a", session_id=session.session_id,
        )
        self.assertEqual(next_event["cursor"], 2)
        self.assertEqual(next_event["event"]["step_id"], "step-a")
        self.assertEqual(next_event["context"]["run_context"]["request"]["goal"], "revised")
        self.assertEqual(self.persisted_context["objective"], "original objective")

    async def test_edits_require_pause_and_reject_immutable_fields(self):
        session = await self.service.open(tenant_id="tenant-a", run_id="run-a")
        with self.assertRaisesRegex(ValueError, "only be modified while paused"):
            await self.service.update_context(
                tenant_id="tenant-a", run_id="run-a", session_id=session.session_id,
                patch={"objective": "too early"},
            )
        await self.service.pause(
            tenant_id="tenant-a", run_id="run-a", session_id=session.session_id,
        )
        with self.assertRaisesRegex(ValueError, "immutable or unsupported"):
            await self.service.update_context(
                tenant_id="tenant-a", run_id="run-a", session_id=session.session_id,
                patch={"version": 99},
            )
        with self.assertRaisesRegex(ValueError, "non-empty"):
            await self.service.update_context(
                tenant_id="tenant-a", run_id="run-a", session_id=session.session_id, patch={},
            )


class ReplayPauseModifyResumeApiTests(unittest.TestCase):
    def setUp(self):
        self.allowed = True
        self.store = InMemoryRunStateStore()
        self.repository = InMemoryRunTraceRepository()
        self.trace = RunTraceService(repository=self.repository)

        async def resolve_context(_tenant):
            class Context:
                async def rebuild(inner, *, tenant_id, run_id):
                    return {"phase": "execute", "version": 3,
                            "memory_snapshot_ref": "memory:3", "objective": "initial"}
            return Context()

        async def resolve_replay(_principal):
            return self.replay

        async def principal(_request):
            return Principal(user_id="user-a", tenant_id="tenant-a", session_id="session-a",
                             email="user@example.test", display_name="User")

        async def authorize(_principal, resource_type, resource_id, action):
            return self.allowed and resource_type == "run" and action in {"read", "rerun"}

        asyncio.run(self.store.create("tenant-a", "run-api"))
        asyncio.run(self.trace.emit(
            tenant_id="tenant-a", run_id="run-api", event_type=RunTraceEventType.STEP_START,
            step_id="step-api", payload={"replay_input": {"task": "api"}},
        ))
        self.replay = RunReplayService(
            lifecycle_store=self.store, trace_repository=self.repository,
            resolve_context_service=resolve_context,
        )
        app = FastAPI()
        app.include_router(create_run_replay_router(
            resolve_principal=principal, resolve_replay=resolve_replay, authorize=authorize,
        ))
        self.client = TestClient(app)

    def test_api_pause_patch_resume_and_authorization(self):
        opened = self.client.post("/api/platform/v1/runs/run-api/replay")
        session_id = opened.json()["session_id"]
        base = f"/api/platform/v1/runs/run-api/replay/{session_id}"
        self.client.post(f"{base}/pause")
        patched = self.client.patch(f"{base}/context", json={"objective": "changed"})
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["context"]["run_context"]["objective"], "changed")
        self.assertFalse(self.client.post(f"{base}/resume").json()["paused"])
        self.allowed = False
        self.assertEqual(
            self.client.patch(f"{base}/context", json={"objective": "forbidden"}).status_code, 403,
        )

    def test_api_rejects_empty_or_non_object_context_patch(self):
        opened = self.client.post("/api/platform/v1/runs/run-api/replay")
        base = f"/api/platform/v1/runs/run-api/replay/{opened.json()['session_id']}"
        self.client.post(f"{base}/pause")
        self.assertEqual(self.client.patch(f"{base}/context", json={}).status_code, 400)
        self.assertEqual(self.client.patch(f"{base}/context", json=[]).status_code, 400)


if __name__ == "__main__":
    unittest.main()
