from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class TaskCenterAndInterventionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        self.tenant_id = "task-it-" + uuid4().hex
        self.agent_id = "agent-" + uuid4().hex
        self.template_id = "model-" + uuid4().hex
        self.run_id = uuid4()
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id,display_name) VALUES (%s,%s)",
                (self.tenant_id, "Task Center Integration"),
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
            await conn.execute(
                """UPDATE platform_runs SET checkpoint_ref='checkpoint-1'
                   WHERE tenant_id=%s AND run_id=%s""",
                (self.tenant_id, self.run_id),
            )
            await conn.commit()
        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)
        self.connect = connect

    async def test_task_submit_idempotency_queue_and_completion(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService, TaskQueueWorker
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        first = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "analyze"},
            idempotency_key="request-1",
        )
        repeated = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "analyze"},
            idempotency_key="request-1",
        )
        self.assertEqual(first["task_id"], repeated["task_id"])
        worker = TaskQueueWorker(
            service=service, worker_id="worker-a",
            execute_task=lambda lease: _task_result(str(self.run_id)),
        )
        self.assertTrue(await worker.run_once())
        saved = await service.get_task(tenant_id=self.tenant_id, task_id=first["task_id"])
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["run_id"], str(self.run_id))
        self.assertEqual(saved["output"], {"answer": "done"})
        listed = await service.list_tasks(
            tenant_id=self.tenant_id, actor_id="user-a",
        )
        self.assertEqual(len(listed), 1)

    async def test_idempotency_key_cannot_be_reused_for_different_input(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService, TaskCenterError
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "one"},
            idempotency_key="same-key",
        )
        with self.assertRaises(TaskCenterError):
            await service.submit_task(
                tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
                project_id=None, session_id=None, input={"goal": "different"},
                idempotency_key="same-key",
            )

    async def test_running_task_cancellation_calls_run_handler_and_finishes(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService
        cancelled_runs = []
        async def cancel_run(tenant_id, run_id):
            cancelled_runs.append((tenant_id, run_id))
            return {"status": "cancelled"}
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
            cancel_run=cancel_run,
        )
        task = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "cancel"},
        )
        lease = await service.claim_next(worker_id="worker-cancel")
        await service.set_run_id(lease, str(self.run_id))
        result = await service.cancel_task(
            tenant_id=self.tenant_id, task_id=task["task_id"], actor_id="user-a",
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(cancelled_runs, [(self.tenant_id, str(self.run_id))])

    async def test_running_task_waits_for_run_cancellation_acknowledgement(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService
        async def cancel_run(tenant_id, run_id):
            return {"status": "cancelling"}
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
            cancel_run=cancel_run,
        )
        task = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "cancel safely"},
        )
        lease = await service.claim_next(worker_id="worker-cancel-pending")
        await service.set_run_id(lease, str(self.run_id))
        result = await service.cancel_task(
            tenant_id=self.tenant_id, task_id=task["task_id"], actor_id="user-a",
        )
        self.assertEqual(result["status"], "cancelling")

    async def test_expired_cancelling_task_is_reclaimed_only_to_acknowledge(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService, TaskQueueWorker
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        task = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "cancel after crash"},
        )
        await service.claim_next(worker_id="worker-before-crash")
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_tasks SET status='cancelling',
                   lease_expires_at=now()-interval '1 second'
                   WHERE tenant_id=%s AND task_id=%s""",
                (self.tenant_id, task["task_id"]),
            )
        async def must_not_execute(_lease):
            raise AssertionError("a cancelling task must not be executed again")
        worker = TaskQueueWorker(
            service=service, worker_id="worker-after-crash",
            execute_task=must_not_execute,
        )
        self.assertTrue(await worker.run_once())
        saved = await service.get_task(tenant_id=self.tenant_id, task_id=task["task_id"])
        self.assertEqual(saved["status"], "cancelled")

    async def test_running_task_cancellation_requires_handler_and_restores_state(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService, TaskCenterError
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        task = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "cancel"},
        )
        lease = await service.claim_next(worker_id="worker-cancel")
        await service.set_run_id(lease, str(self.run_id))
        with self.assertRaises(TaskCenterError):
            await service.cancel_task(
                tenant_id=self.tenant_id, task_id=task["task_id"], actor_id="user-a",
            )
        saved = await service.get_task(tenant_id=self.tenant_id, task_id=task["task_id"])
        self.assertEqual(saved["status"], "running")

    async def test_worker_cannot_claim_tasks_from_another_tenant(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService
        other_tenant = self.tenant_id + "-other"
        other_agent = self.agent_id + "-other"
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id,display_name) VALUES (%s,%s)",
                (other_tenant, "Other Tenant"),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id,agent_id,display_name,role_definition,status)
                   VALUES (%s,%s,%s,%s,'active')""",
                (other_tenant, other_agent, "Other Agent", "integration"),
            )
        other_service = PostgresTaskCenterService(
            tenant_id=other_tenant, connection_factory=self.connect,
        )
        await other_service.submit_task(
            tenant_id=other_tenant, actor_id="user-b", agent_id=other_agent,
            project_id=None, session_id=None, input={"goal": "private"},
        )
        own_service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        self.assertIsNone(await own_service.claim_next(worker_id="tenant-a-worker"))

    async def test_task_queue_claims_higher_priority_first(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        low = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "low"}, priority=10,
        )
        high = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "high"}, priority=900,
        )
        lease = await service.claim_next(worker_id="worker-priority")
        self.assertEqual(lease.task_id, high["task_id"])
        self.assertNotEqual(lease.task_id, low["task_id"])

    async def test_task_failures_retry_then_enter_terminal_failed_state(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        task = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "retry"},
        )
        async with await self.connect() as conn:
            await conn.execute(
                "UPDATE platform_tasks SET max_attempts=2 WHERE tenant_id=%s AND task_id=%s",
                (self.tenant_id, task["task_id"]),
            )
        lease = await service.claim_next(worker_id="worker-a")
        self.assertEqual(lease.attempts, 1)
        await service.fail(lease, error="temporary")
        async with await self.connect() as conn:
            await conn.execute(
                "UPDATE platform_tasks SET available_at=now() WHERE tenant_id=%s AND task_id=%s",
                (self.tenant_id, task["task_id"]),
            )
        lease = await service.claim_next(worker_id="worker-b")
        self.assertEqual(lease.attempts, 2)
        await service.fail(lease, error="permanent")
        saved = await service.get_task(tenant_id=self.tenant_id, task_id=task["task_id"])
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["last_error"], "permanent")

    async def test_expired_cancelling_task_waits_for_run_terminal_state(self):
        from agent_platform.control_plane.tasks import PostgresTaskCenterService, TaskQueueWorker
        async def cancel_run(tenant_id, run_id):
            return {"status": "cancelling"}
        service = PostgresTaskCenterService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
            cancel_run=cancel_run,
        )
        task = await service.submit_task(
            tenant_id=self.tenant_id, actor_id="user-a", agent_id=self.agent_id,
            project_id=None, session_id=None, input={"goal": "wait for Run stop"},
        )
        lease = await service.claim_next(worker_id="worker-before-crash")
        await service.set_run_id(lease, str(self.run_id))
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_tasks SET status='cancelling',
                   lease_expires_at=now()-interval '1 second'
                   WHERE tenant_id=%s AND task_id=%s""",
                (self.tenant_id, task["task_id"]),
            )
        async def must_not_execute(_lease):
            raise AssertionError("a cancelling task must not be executed again")
        worker = TaskQueueWorker(
            service=service, worker_id="worker-after-crash",
            execute_task=must_not_execute,
        )
        self.assertTrue(await worker.run_once())
        saved = await service.get_task(tenant_id=self.tenant_id, task_id=task["task_id"])
        self.assertEqual(saved["status"], "cancelling")
        self.assertIsNone(saved["finished_at"])

    async def test_intervention_timeline_and_checkpoint_gated_resume(self):
        from agent_platform.runtime.interventions import (
            PostgresRunDetailService,
            RunInterventionService,
        )
        from agent_platform.runtime.lifecycle import RunLifecycleManager
        from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
        interventions = RunInterventionService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        item = await interventions.submit(
            tenant_id=self.tenant_id, run_id=str(self.run_id), actor_id="user-a",
            kind="context", payload={"additional_context": "Use the latest approved data"},
        )
        self.assertEqual(item["status"], "pending")
        applied_payloads = []
        async def apply(payload):
            applied_payloads.append(payload)
        self.assertEqual(await interventions.apply_pending(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
            worker_id="worker-a", applier=apply,
        ), 1)
        self.assertEqual(applied_payloads[0]["payload"]["additional_context"],
                         "Use the latest approved data")
        rows = await interventions.list_interventions(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
        )
        self.assertEqual(rows[0]["status"], "applied")
        events = await interventions.list_events(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            ["human.intervention_submitted", "human.intervention_applied"],
        )
        manager = RunLifecycleManager(PostgresRunStateStore(self.connect))
        resumed = await manager.request_resume(self.tenant_id, str(self.run_id))
        self.assertEqual(resumed.status.value, "interrupted")
        await interventions.record_event(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
            event_type="run.step.failed", actor_id="worker-a",
            payload={"step_id": "publish", "error": "upstream unavailable"},
        )
        approval_id = uuid4()
        async with await self.connect() as conn:
            await conn.execute(
                """INSERT INTO platform_approvals
                   (tenant_id,approval_id,run_id,action,resource,risk_level,action_hash,
                    requested_by,status,expires_at)
                   VALUES (%s,%s,%s,'publish','report.pdf',3,'digest-1',
                           'requester-a','approved',now()+interval '10 minutes')""",
                (self.tenant_id, approval_id, self.run_id),
            )
            await conn.execute(
                "UPDATE platform_runs SET last_error='step publish failed' WHERE tenant_id=%s AND run_id=%s",
                (self.tenant_id, self.run_id),
            )
            await conn.commit()
        details = PostgresRunDetailService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        detail = await details.get(tenant_id=self.tenant_id, run_id=str(self.run_id))
        self.assertEqual(detail["checkpoint_ref"], "checkpoint-1")

    async def test_postgres_checkpoint_survives_worker_restart_and_resumes(self):
        try:
            from langgraph.graph import END, START, StateGraph
            if importlib.util.find_spec("langgraph.checkpoint.postgres.aio") is None:
                self.skipTest("LangGraph PostgreSQL checkpointer extra is unavailable")
            from typing import TypedDict

            from agent_platform.runtime.lifecycle import RunLifecycleManager, RunStatus
            from agent_platform.runtime.postgres_checkpointer import postgres_run_executor
            from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
        except ImportError as exc:
            self.skipTest(f"LangGraph PostgreSQL runtime dependencies are unavailable: {exc}")

        run_id = uuid4()
        async with await self.connect() as conn:
            await conn.execute(
                """INSERT INTO platform_runs
                   (tenant_id,run_id,agent_id,template_id,template_version,status,input)
                   VALUES (%s,%s,%s,%s,1,'queued','{}'::jsonb)""",
                (self.tenant_id, run_id, self.agent_id, self.template_id),
            )
            await conn.commit()

        store = PostgresRunStateStore(self.connect)
        lifecycle = RunLifecycleManager(store)
        step_one_calls = 0
        step_two_calls = 0
        fail_first_step_two = True

        class RecoveryState(TypedDict, total=False):
            value: int
            done: bool

        def build_graph():
            graph = StateGraph(RecoveryState)

            async def step_one(state):
                nonlocal step_one_calls
                step_one_calls += 1
                return {"value": state.get("value", 0) + 1}

            async def step_two(state):
                nonlocal step_two_calls, fail_first_step_two
                step_two_calls += 1
                if fail_first_step_two:
                    fail_first_step_two = False
                    raise asyncio.CancelledError()
                return {"done": True}

            graph.add_node("step_one", step_one)
            graph.add_node("step_two", step_two)
            graph.add_edge(START, "step_one")
            graph.add_edge("step_one", "step_two")
            graph.add_edge("step_two", END)
            return graph

        async with postgres_run_executor(
            build_graph(), DSN, lifecycle=lifecycle, worker_id="worker-before-restart",
        ) as executor:
            with self.assertRaises(asyncio.CancelledError):
                await executor.execute(
                    tenant_id=self.tenant_id, run_id=str(run_id), input={"value": 0},
                )

        interrupted = await store.get(self.tenant_id, str(run_id))
        self.assertIsNotNone(interrupted)
        self.assertEqual(interrupted.status, RunStatus.INTERRUPTED)
        self.assertTrue(interrupted.checkpoint_ref)
        self.assertEqual(step_one_calls, 1)
        self.assertEqual(step_two_calls, 1)

        self.assertTrue(await store.acquire_lease(
            self.tenant_id, str(run_id), "worker-after-restart",
        ))
        async with postgres_run_executor(
            build_graph(), DSN, lifecycle=lifecycle, worker_id="worker-after-restart",
        ) as executor:
            result = await executor.resume_claimed(
                snapshot=interrupted, input={"value": 0},
            )

        completed = await store.get(self.tenant_id, str(run_id))
        self.assertIsNotNone(completed)
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(step_one_calls, 1, "recovery must not replay the completed checkpointed node")
        self.assertEqual(step_two_calls, 2)
        self.assertTrue(result["done"])

    async def test_run_detail_timeline_records_steps_logs_artifacts_and_redacts_secrets(self):
        from agent_platform.runtime.interventions import (
            PostgresRunDetailService,
            RunInterventionService,
        )
        events = RunInterventionService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        await events.record_event(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
            event_type="run.step.started", actor_id="worker-a",
            payload={"step_id": "retrieve", "input": {"query": "safe"}},
        )
        await events.record_event(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
            event_type="run.log", actor_id="worker-a",
            payload={"level": "info", "message": "Authorization: Bearer abc.def.secret"},
        )
        await events.record_event(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
            event_type="run.artifact.created", actor_id="worker-a",
            payload={"artifact_id": "artifact-1", "name": "report.pdf"},
        )
        await events.record_event(
            tenant_id=self.tenant_id, run_id=str(self.run_id),
            event_type="run.step.failed", actor_id="worker-a",
            payload={"step_id": "publish", "error": "upstream unavailable"},
        )
        approval_id = uuid4()
        async with await self.connect() as conn:
            await conn.execute(
                """INSERT INTO platform_approvals
                   (tenant_id,approval_id,run_id,action,resource,risk_level,action_hash,
                    requested_by,status,expires_at)
                   VALUES (%s,%s,%s,'publish','report.pdf',3,'digest-1',
                           'requester-a','approved',now()+interval '10 minutes')""",
                (self.tenant_id, approval_id, self.run_id),
            )
            await conn.execute(
                "UPDATE platform_runs SET last_error='step publish failed' WHERE tenant_id=%s AND run_id=%s",
                (self.tenant_id, self.run_id),
            )
            await conn.commit()
        details = PostgresRunDetailService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        detail = await details.get(tenant_id=self.tenant_id, run_id=str(self.run_id))
        self.assertEqual(detail["input"], {})
        self.assertEqual(detail["checkpoint_ref"], "checkpoint-1")
        self.assertEqual(
            [item["event_type"] for item in detail["steps"]],
            ["run.step.started", "run.step.failed"],
        )
        self.assertEqual(detail["logs"][0]["payload"]["message"], "Authorization: Bearer [REDACTED]")
        self.assertEqual(detail["artifacts"][0]["payload"]["artifact_id"], "artifact-1")
        self.assertEqual(detail["last_error"], "step publish failed")
        self.assertEqual(detail["approvals"][0]["approval_id"], str(approval_id))
        self.assertEqual(detail["approvals"][0]["status"], "approved")
        self.assertEqual(len(detail["events"]), 4)

    async def test_run_event_rejects_cross_tenant_and_invalid_shapes(self):
        from agent_platform.runtime.interventions import RunInterventionService
        events = RunInterventionService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        with self.assertRaises(PermissionError):
            await events.record_event(
                tenant_id="other-tenant", run_id=str(self.run_id),
                event_type="run.log", payload={"level": "info", "message": "x"},
            )
        with self.assertRaises(ValueError):
            await events.record_event(
                tenant_id=self.tenant_id, run_id=str(self.run_id),
                event_type="run.log", payload={"level": "trace", "message": "x"},
            )

    async def test_intervention_is_rejected_for_non_paused_run(self):
        from agent_platform.runtime.interventions import RunInterventionService
        service = RunInterventionService(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        async with await self.connect() as conn:
            await conn.execute(
                "UPDATE platform_runs SET status='running' WHERE tenant_id=%s AND run_id=%s",
                (self.tenant_id, self.run_id),
            )
        with self.assertRaises(ValueError):
            await service.submit(
                tenant_id=self.tenant_id, run_id=str(self.run_id), actor_id="user-a",
                kind="takeover", payload={"reason": "operator takeover"},
            )


async def _task_result(run_id):
    return {"run_id": run_id, "answer": "done"}


if __name__ == "__main__":
    unittest.main()
