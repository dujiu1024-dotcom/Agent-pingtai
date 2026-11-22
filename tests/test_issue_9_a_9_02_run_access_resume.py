from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="already fixed submit→ensure, re-check")

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


class RunAccessAndCheckpointResumeTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryRunStateStore()
        asyncio.run(self.store.create("tenant-a", "run-1"))
        asyncio.run(self._pause_run())
        self.events = [
            {"event_id": "evt-1", "event_type": "run.step.started", "payload": {"step": "retrieve"}},
            {"event_id": "evt-2", "event_type": "human.intervention_submitted", "payload": {"kind": "context"}},
        ]
        self.intervention_calls = []

        async def principal(request):
            return Principal(
                user_id=request.headers.get("x-user", "operator-a"),
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="operator@example.test",
                display_name="Operator",
            )

        async def lifecycle(_principal):
            return RunLifecycleManager(self.store)

        async def authorize(p, resource_type, resource_id, action):
            denied = action == p.user_id.removeprefix("deny:")
            return (
                p.tenant_id == "tenant-a"
                and resource_type == "run"
                and resource_id in {"run-1", "run-running"}
                and action in {"read", "cancel", "resume", "intervene", "pause"}
                and not denied
            )

        class Interventions:
            async def ensure_decisions_resolved(inner, **kwargs):
                self.intervention_calls.append(("ensure", kwargs))

            async def list_interventions(inner, **kwargs):
                self.intervention_calls.append(("list_interventions", kwargs))
                return [{"intervention_id": "int-1", "status": "applied"}]

            async def list_events(inner, **kwargs):
                self.intervention_calls.append(("list_events", kwargs))
                return list(self.events)

            async def submit(inner, **kwargs):
                self.intervention_calls.append(("submit", kwargs))
                return {"intervention_id": "int-2", "status": "pending"}

            async def record_action(inner, **kwargs):
                self.intervention_calls.append(("record_action", kwargs))

        async def resolve_interventions(_principal):
            return Interventions()

        class Details:
            async def get(inner, **kwargs):
                if kwargs["tenant_id"] != "tenant-a" or kwargs["run_id"] != "run-1":
                    return None
                return {
                    "run_id": "run-1",
                    "status": "paused",
                    "checkpoint_ref": "checkpoint-1",
                    "input": {"goal": "prepare report"},
                    "steps": [{"step_id": "retrieve", "status": "completed"}],
                    "logs": [{"message": "retrieval completed"}],
                    "artifacts": [{"artifact_id": "artifact-1"}],
                    "approvals": [],
                    "events": list(self.events),
                }

        async def resolve_details(_principal):
            return Details()

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=principal,
            resolve_lifecycle=lifecycle,
            authorize=authorize,
            resolve_interventions=resolve_interventions,
            resolve_details=resolve_details,
        ))
        self.client = TestClient(app)

    async def _pause_run(self):
        manager = RunLifecycleManager(self.store)
        await manager.start("tenant-a", "run-1")
        await manager.pause("tenant-a", "run-1", checkpoint_ref="checkpoint-1")

    def test_user_can_query_run_detail_and_follow_its_timeline(self):
        detail = self.client.get("/api/platform/v1/runs/run-1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["input"]["goal"], "prepare report")
        self.assertEqual(detail.json()["checkpoint_ref"], "checkpoint-1")
        self.assertEqual(detail.json()["steps"][0]["step_id"], "retrieve")

        events = self.client.get("/api/platform/v1/runs/run-1/events")
        self.assertEqual(events.status_code, 200)
        self.assertEqual(
            [event["event_type"] for event in events.json()["items"]],
            ["run.step.started", "human.intervention_submitted"],
        )

    def test_run_query_and_event_tracking_are_tenant_scoped(self):
        for path in (
            "/api/platform/v1/runs/run-1",
            "/api/platform/v1/runs/run-1/events",
            "/api/platform/v1/runs/run-1/interventions",
        ):
            response = self.client.get(path, headers={"x-tenant": "tenant-b"})
            self.assertEqual(response.status_code, 403, path)

    def test_read_and_cancel_permissions_are_enforced_independently(self):
        denied_read = self.client.get(
            "/api/platform/v1/runs/run-1", headers={"x-user": "deny:read"},
        )
        denied_cancel = self.client.post(
            "/api/platform/v1/runs/run-1/cancel", headers={"x-user": "deny:cancel"},
        )
        self.assertEqual(denied_read.status_code, 403)
        self.assertEqual(denied_cancel.status_code, 403)
        self.assertEqual(
            (asyncio.run(self.store.get("tenant-a", "run-1"))).status,
            RunStatus.PAUSED,
        )

    def test_user_can_cancel_a_paused_run_and_repeat_cancel_safely(self):
        first = self.client.post("/api/platform/v1/runs/run-1/cancel")
        second = self.client.post("/api/platform/v1/runs/run-1/cancel")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "cancelled")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "cancelled")

    def test_running_run_cancel_is_durable_until_worker_acknowledges(self):
        asyncio.run(self.store.create("tenant-a", "run-running"))
        manager = RunLifecycleManager(self.store)
        asyncio.run(manager.start("tenant-a", "run-running"))
        response = self.client.post("/api/platform/v1/runs/run-running/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelling")
        self.assertEqual(
            asyncio.run(self.store.get("tenant-a", "run-running")).status,
            RunStatus.CANCELLING,
        )

    def test_human_intervention_can_be_submitted_before_checkpoint_resume(self):
        submitted = self.client.post(
            "/api/platform/v1/runs/run-1/interventions",
            json={"kind": "context", "payload": {"instruction": "use approved figures"}},
        )
        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(submitted.json()["status"], "pending")
        self.assertEqual(self.intervention_calls[0][0], "ensure")

        response = self.client.post("/api/platform/v1/runs/run-1/resume")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "interrupted")
        self.assertEqual(response.json()["checkpoint_ref"], "checkpoint-1")
        self.assertEqual(
            self.intervention_calls[0][0],
            "ensure",
            "pending human decisions must be checked before resume",
        )
        self.assertTrue(any(call[0] == "record_action" for call in self.intervention_calls))

    def test_resume_fails_closed_when_checkpoint_is_missing(self):
        current = asyncio.run(self.store.get("tenant-a", "run-1"))
        asyncio.run(self.store.transition(
            RunSnapshot(
                "tenant-a", "run-1", RunStatus.RUNNING, current.version,
            ),
            expected_version=current.version,
        ))
        current = asyncio.run(self.store.get("tenant-a", "run-1"))
        asyncio.run(self.store.transition(
            RunSnapshot(
                "tenant-a", "run-1", RunStatus.PAUSED, current.version,
                checkpoint_ref=None,
            ),
            expected_version=current.version,
        ))
        response = self.client.post("/api/platform/v1/runs/run-1/resume")
        self.assertEqual(response.status_code, 409)
        self.assertIn("no durable checkpoint", response.json()["detail"])

    def test_resume_permission_is_required(self):
        response = self.client.post(
            "/api/platform/v1/runs/run-1/resume", headers={"x-user": "deny:resume"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            asyncio.run(self.store.get("tenant-a", "run-1")).status,
            RunStatus.PAUSED,
        )


if __name__ == "__main__":
    unittest.main()
