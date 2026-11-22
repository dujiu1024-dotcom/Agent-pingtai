from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.runs import create_runs_router
from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


class HumanCollaborationApiTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryRunStateStore()
        asyncio.run(self.store.create("tenant-a", "run-1"))
        self.calls = []

        async def principal(request):
            return Principal(
                user_id="operator-a", tenant_id="tenant-a", session_id="session",
                email="operator@example.test", display_name="Operator",
            )

        async def lifecycle(_principal):
            return RunLifecycleManager(self.store)

        async def authorize(_principal, resource_type, resource_id, action):
            return resource_type == "run" and resource_id == "run-1" and action in {
                "read", "intervene", "resume",
            }

        class Interventions:
            async def decide(inner, **kwargs):
                self.calls.append(("decide", kwargs))
                return {"intervention_id": kwargs["intervention_id"],
                        "decision": kwargs["decision"], "status": "applied"}

            async def revise_result(inner, **kwargs):
                self.calls.append(("revise", kwargs))
                return {"run_id": kwargs["run_id"], "output": dict(kwargs["output"])}

        async def interventions(_principal):
            return Interventions()

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=principal, resolve_lifecycle=lifecycle,
            authorize=authorize, resolve_interventions=interventions,
        ))
        self.client = TestClient(app)

    def test_decision_requires_intervention_permission_and_records_actor(self):
        response = self.client.post(
            "/api/platform/v1/runs/run-1/interventions/00000000-0000-0000-0000-000000000001/decision",
            json={"decision": "approved", "rationale": "Reviewed evidence"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["decision"], "approved")
        self.assertEqual(self.calls[0][1]["actor_id"], "operator-a")

    def test_result_revision_requires_reason_and_preserves_output_shape(self):
        response = self.client.post(
            "/api/platform/v1/runs/run-1/result/revisions",
            json={"output": {"answer": "corrected"}, "reason": "Operator correction"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output"], {"answer": "corrected"})
        self.assertEqual(self.calls[0][0], "revise")

    def test_result_revision_rejects_missing_reason(self):
        response = self.client.post(
            "/api/platform/v1/runs/run-1/result/revisions",
            json={"output": {"answer": "corrected"}},
        )
        self.assertEqual(response.status_code, 400)


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class HumanCollaborationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row

        self.tenant_id = "human-it-" + uuid4().hex
        self.agent_id = "agent-" + uuid4().hex
        self.template_id = "model-" + uuid4().hex
        self.run_id = uuid4()
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id,display_name) VALUES (%s,%s)",
                (self.tenant_id, "Human Collaboration Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id,agent_id,display_name,role_definition,status)
                   VALUES (%s,%s,%s,%s,'active')""",
                (self.tenant_id, self.agent_id, "Test Agent", "integration"),
            )
            await conn.execute(
                """INSERT INTO platform_model_templates
                   (tenant_id,template_id,version,definition,status,created_by)
                   VALUES (%s,%s,1,'{}'::jsonb,'active','integration')""",
                (self.tenant_id, self.template_id),
            )
            await conn.execute(
                """INSERT INTO platform_runs
                   (tenant_id,run_id,agent_id,template_id,template_version,status,input)
                   VALUES (%s,%s,%s,%s,1,'paused','{}'::jsonb)""",
                (self.tenant_id, self.run_id, self.agent_id, self.template_id),
            )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)
        self.connect = connect

    async def test_cancel_and_pause_actor_actions_persist_to_run_timeline(self):
        from agent_platform.runtime.interventions import RunInterventionService

        service = RunInterventionService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        for event_type, status in (
            ("human.run_cancel_requested", "cancelling"),
            ("human.run_paused", "paused"),
        ):
            recorded = await service.record_action(
                tenant_id=self.tenant_id, run_id=str(self.run_id),
                actor_id="operator-a", event_type=event_type,
                payload={"status": status, "version": 3},
            )
            self.assertEqual(recorded["event_type"], event_type)
            self.assertEqual(recorded["actor_id"], "operator-a")
        events = await service.list_events(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
        )
        self.assertEqual(
            {event["event_type"] for event in events},
            {"human.run_cancel_requested", "human.run_paused"},
        )
        self.assertTrue(all(event["actor_id"] == "operator-a" for event in events))
        self.assertEqual(events[0]["payload"]["status"], "cancelling")
        with self.assertRaises(PermissionError):
            await service.record_action(
                tenant_id="another-tenant", run_id=str(self.run_id),
                actor_id="operator-a", event_type="human.run_paused",
            )

    async def test_rejected_approval_blocks_run_continuation(self):
        from agent_platform.runtime.interventions import RunInterventionService

        service = RunInterventionService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        request = await service.submit(
            tenant_id=self.tenant_id, run_id=str(self.run_id), actor_id="requester",
            kind="approval", payload={"action": "publish", "resource": "report"},
        )
        await service.decide(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
            intervention_id=request["intervention_id"], actor_id="approver",
            decision="rejected", rationale="Approval denied",
        )
        with self.assertRaises(ValueError):
            await service.ensure_decisions_resolved(
                tenant_id=self.tenant_id, run_id=str(self.run_id),
            )

    async def test_approval_decision_result_revision_and_audit_timeline(self):
        from agent_platform.runtime.interventions import RunInterventionService

        service = RunInterventionService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        request = await service.submit(
            tenant_id=self.tenant_id, run_id=str(self.run_id), actor_id="requester",
            kind="approval", payload={"action": "publish", "resource": "report"},
        )
        with self.assertRaises(ValueError):
            await service.ensure_decisions_resolved(
                tenant_id=self.tenant_id, run_id=str(self.run_id),
            )
        self.assertEqual(await service.apply_pending(
            tenant_id=self.tenant_id, run_id=str(self.run_id), worker_id="worker-before-decision",
            applier=lambda payload: _capture([], payload),
        ), 0)
        decision = await service.decide(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
            intervention_id=request["intervention_id"], actor_id="approver",
            decision="approved", rationale="Evidence reviewed",
        )
        self.assertEqual(decision["status"], "pending")
        await service.ensure_decisions_resolved(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
        )
        applied = []
        self.assertEqual(await service.apply_pending(
            tenant_id=self.tenant_id, run_id=str(self.run_id), worker_id="worker-a",
            applier=lambda payload: _capture(applied, payload),
        ), 1)
        self.assertEqual(applied[0]["payload"]["decision"], "approved")

        from psycopg.types.json import Jsonb
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs SET status='succeeded',output=%s
                   WHERE tenant_id=%s AND run_id=%s""",
                (Jsonb({"answer": "draft"}), self.tenant_id, self.run_id),
            )
            await conn.commit()

        revised = await service.revise_result(
            tenant_id=self.tenant_id, run_id=str(self.run_id), actor_id="operator",
            output={"answer": "corrected"}, reason="Corrected factual error",
        )
        self.assertEqual(revised["output"], {"answer": "corrected"})
        events = await service.list_events(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            ["human.intervention_submitted", "human.intervention_decided",
             "human.intervention_applied", "human.result_revised"],
        )


async def _capture(items, payload):
    items.append(payload)


if __name__ == "__main__":
    unittest.main()
