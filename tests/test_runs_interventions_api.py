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

        async def intervention_resolver(p):
            return Interventions()

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=principal, resolve_lifecycle=lifecycle,
            authorize=authorize, resolve_interventions=intervention_resolver,
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

    def test_resume_requires_checkpoint_and_changes_to_recoverable_state(self):
        response = self.client.post("/api/platform/v1/runs/run-1/resume")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "interrupted")

    def test_intervention_is_not_visible_cross_tenant(self):
        response = self.client.get(
            "/api/platform/v1/runs/run-1/interventions",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
