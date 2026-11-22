from __future__ import annotations

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.runs import create_runs_router
from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import (
    InMemoryRunStateStore,
    RunLifecycleManager,
    RunSnapshot,
    RunStatus,
)


class InterventionApiTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryRunStateStore()
        asyncio.run(self.store.create("tenant-a", "run-1"))
        asyncio.run(self.manager_start_paused())
        self.records = []
        self.events = []
        self.audit_records = []

        async def principal(request):
            return Principal(
                user_id="operator-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="operator@example.test", display_name="Operator",
            )

        async def lifecycle(p):
            return RunLifecycleManager(self.store)

        async def authorize(p, resource_type, resource_id, action):
            return resource_type == "run" and action in {
                "read", "cancel", "resume", "intervene", "pause",
            }

        class Interventions:
            async def submit(inner, **kwargs):
                self.records.append(kwargs)
                return {"intervention_id": "intervention-1", "status": "pending"}
            async def list_interventions(inner, **kwargs):
                return [{"intervention_id": "intervention-1", "status": "pending"}]
            async def list_events(inner, **kwargs):
                return [{"event_type": "human.intervention_submitted"}]
            async def record_action(inner, **kwargs):
                self.audit_records.append(kwargs)
                return {"event_type": kwargs["event_type"], **kwargs}

        async def intervention_resolver(p):
            return Interventions()

        class Details:
            async def get(inner, **kwargs):
                if kwargs["tenant_id"] != "tenant-a":
                    return None
                return {
                    "run_id": "run-1", "status": "paused", "input": {"goal": "demo"},
                    "output": {"answer": "done"}, "steps": [{"step_id": "one"}],
                    "logs": [{"message": "completed"}], "artifacts": [{"artifact_id": "a1"}],
                    "last_error": None, "approvals": [{"status": "approved"}],
                    "task_id": "task-1", "trace_id": "trace-1", "events": [],
                }

        async def detail_resolver(p):
            return Details()

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=principal, resolve_lifecycle=lifecycle,
            authorize=authorize, resolve_interventions=intervention_resolver,
            resolve_details=detail_resolver,
        ))
        self.client = TestClient(app)

    async def manager_start_paused(self):
        current = await self.store.get("tenant-a", "run-1")
        await self.store.transition(
            RunSnapshot("tenant-a", "run-1", RunStatus.RUNNING, current.version),
            expected_version=current.version,
        )
        current = await self.store.get("tenant-a", "run-1")
        await self.store.transition(
            RunSnapshot(
                "tenant-a", "run-1", RunStatus.PAUSED, current.version,
                checkpoint_ref="checkpoint-1",
            ),
            expected_version=current.version,
        )

    def test_submit_intervention_and_read_timeline(self):
        response = self.client.post(
            "/api/platform/v1/runs/run-1/interventions",
            json={"kind": "takeover", "payload": {"reason": "operator taking over"}},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.records[0]["kind"], "takeover")
        self.assertEqual(
            self.client.get("/api/platform/v1/runs/run-1/interventions").json()["items"][0]["status"],
            "pending",
        )
        self.assertEqual(
            self.client.get("/api/platform/v1/runs/run-1/events").json()["items"][0]["event_type"],
            "human.intervention_submitted",
        )

    def test_run_detail_returns_io_timeline_approvals_and_trace_links(self):
        response = self.client.get("/api/platform/v1/runs/run-1")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["input"], {"goal": "demo"})
        self.assertEqual(detail["output"], {"answer": "done"})
        self.assertEqual(detail["steps"][0]["step_id"], "one")
        self.assertEqual(detail["logs"][0]["message"], "completed")
        self.assertEqual(detail["artifacts"][0]["artifact_id"], "a1")
        self.assertEqual(detail["approvals"][0]["status"], "approved")
        self.assertEqual(detail["task_id"], "task-1")
        self.assertEqual(detail["trace_id"], "trace-1")

    def test_resume_requires_checkpoint_and_changes_to_recoverable_state(self):
        response = self.client.post("/api/platform/v1/runs/run-1/resume")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "interrupted")

    def test_cancel_records_actor_and_resulting_lifecycle_state(self):
        response = self.client.post("/api/platform/v1/runs/run-1/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertEqual(len(self.audit_records), 1)
        self.assertEqual(self.audit_records[0]["tenant_id"], "tenant-a")
        self.assertEqual(self.audit_records[0]["run_id"], "run-1")
        self.assertEqual(self.audit_records[0]["actor_id"], "operator-a")
        self.assertEqual(self.audit_records[0]["event_type"], "human.run_cancel_requested")
        self.assertEqual(self.audit_records[0]["payload"]["status"], "cancelled")
        self.assertEqual(self.audit_records[0]["payload"]["version"], response.json()["version"])

    def test_intervention_is_not_visible_cross_tenant(self):
        response = self.client.get(
            "/api/platform/v1/runs/run-1/interventions",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
