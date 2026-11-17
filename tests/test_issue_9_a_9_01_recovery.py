"""Issue #9 A-9-01: durable worker recovery and external-effect replay contracts.

These tests use PostgreSQL when AGENT_PLATFORM_TEST_DSN is configured. They
simulate a process boundary by constructing fresh worker/gateway instances
against the same durable stores; no external provider or production service is
called.
"""
from __future__ import annotations

import os
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from agent_platform.approval.policy import RiskLevel
from agent_platform.runtime.lifecycle import RunLifecycleManager, RunStatus
from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
from agent_platform.runtime.workbench_tasks import RunExecutionWorker
from agent_platform.tools.gateway import SupervisedToolGateway, ToolDefinition
from agent_platform.tools.idempotency import PostgresToolEffectStore

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row


class _Limiter:
    @asynccontextmanager
    async def slot(self, **_kwargs):
        yield object()


class _Audit:
    def __init__(self):
        self.records = []

    async def record(self, **record):
        self.records.append(record)


class _Approval:
    async def consume(self, **_kwargs):
        return True


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class Issue9RecoveryPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"issue-9-a-9-01-{uuid4().hex}"
        migration_dir = Path("agent_platform/storage/postgres")
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)
        self.connect = connect
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "Issue 9 recovery integration"),
            )
            await conn.commit()
        self.store = PostgresRunStateStore(self.connect)

    async def _create_task_run(self):
        run_id = str(uuid4())
        digest = "9" * 64
        await self.store.create_task_run(
            tenant_id=self.tenant_id,
            run_id=run_id,
            actor_id="recovery-actor",
            agent_id="agent-recovery",
            agent_version=1,
            template_id="template-recovery",
            template_version=1,
            input={"prompt": "resume from durable checkpoint"},
            preparation_owner="preparation-worker",
            registry_snapshot=[
                {"kind": "agent", "item_id": "agent-recovery", "version": 1},
            ],
            idempotency_key=f"issue-9-{run_id}",
            request_digest=digest,
        )
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs SET registry_snapshot_digest=%s
                   WHERE tenant_id=%s AND run_id=%s""",
                (digest, self.tenant_id, run_id),
            )
            await conn.commit()
        self.assertTrue(await self.store.mark_task_ready(
            self.tenant_id, run_id, digest, "preparation-worker",
        ))
        return run_id

    async def test_new_worker_process_resumes_expired_running_task_from_checkpoint(self):
        run_id = await self._create_task_run()
        first_claim = await self.store.claim_workbench_tasks(
            "worker-before-restart", limit=1, lease_seconds=30,
        )
        self.assertEqual([item.run_id for item in first_claim], [run_id])
        lifecycle_before_restart = RunLifecycleManager(self.store)
        await lifecycle_before_restart.start(self.tenant_id, run_id)
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs
                   SET checkpoint_ref=%s, lease_expires_at=now()-interval '1 second'
                   WHERE tenant_id=%s AND run_id=%s""",
                ("checkpoint-after-tool-step", self.tenant_id, run_id),
            )
            await conn.commit()

        # A fresh worker/lifecycle object represents a new service process.
        lifecycle_after_restart = RunLifecycleManager(self.store)
        resumed = []

        class RestartedExecutor:
            worker_id = "worker-after-restart"

            def __init__(self):
                self.lifecycle = lifecycle_after_restart

            async def execute(self, **_kwargs):
                raise AssertionError("recovered task must not restart from initial input")

            async def resume_claimed(self, *, snapshot, input, session_context=None):
                resumed.append((snapshot.checkpoint_ref, input, session_context))
                await self.lifecycle.finish(
                    self.tenant_id, run_id, RunStatus.SUCCEEDED,
                    checkpoint_ref=snapshot.checkpoint_ref,
                )
                return {"answer": "resumed", "usage": {"total_tokens": 3}}

        executor = RestartedExecutor()
        worker = RunExecutionWorker(
            run_store=self.store,
            executor_resolver=lambda _record, _worker_id: executor,
            lifecycle_resolver=lambda _tenant: lifecycle_after_restart,
            worker_id="worker-after-restart",
            lease_seconds=30,
            concurrency_limiter=_Limiter(),
            worker_pool_scope_id="issue-9-tests",
            queue_scope_resolver=lambda _record: "default",
        )
        with patch(
            "agent_platform.runtime.workbench_tasks.LangGraphRunExecutor",
            RestartedExecutor,
        ):
            outcome = await worker.run_once()

        self.assertEqual(outcome, {
            "claimed": 1, "succeeded": 1, "failed": 0, "cancelled": 0, "skipped": 0,
        })
        self.assertEqual(
            resumed,
            [("checkpoint-after-tool-step", {"prompt": "resume from durable checkpoint"}, None)],
        )
        record = await self.store.get_task_record(
            self.tenant_id, run_id, actor_id="recovery-actor",
        )
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["output"]["answer"], "resumed")

    async def test_fresh_gateway_replays_confirmed_external_effect_without_second_dispatch(self):
        run_id = await self._create_task_run()
        call_id = "stable-provider-call"
        effect_store_before_restart = PostgresToolEffectStore(self.connect)
        audit_before_restart = _Audit()
        external_dispatches = []

        async def first_handler(arguments):
            external_dispatches.append(dict(arguments))
            return {"provider_receipt": "receipt-123"}

        def resource(arguments):
            return f"external-resource:{arguments['target']}"

        async def authorize(_context, _arguments):
            return True

        def make_gateway(effect_store, audit, handler):
            return SupervisedToolGateway(
                tools=(
                    ToolDefinition(
                        "send_external",
                        RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
                        handler,
                        resource_resolver=resource,
                        resource_authorizer=authorize,
                    ),
                ),
                approval_verifier=_Approval(),
                audit_sink=audit,
                idempotency_store=effect_store,
            )

        request = {
            "tenant_id": self.tenant_id,
            "actor_id": "recovery-actor",
            "actor_roles": ("agent",),
            "tool_name": "send_external",
            "resource": "external-resource:customer-42",
            "arguments": {"target": "customer-42", "message": "approved"},
            "approval_id": "approval-confirmed",
            "run_id": run_id,
            "call_id": call_id,
        }
        gateway_before_restart = make_gateway(
            effect_store_before_restart, audit_before_restart, first_handler,
        )
        first_result = await gateway_before_restart.call(**request)
        self.assertEqual(first_result, {"provider_receipt": "receipt-123"})
        self.assertEqual(len(external_dispatches), 1)

        # Recreate every process-local component; only PostgreSQL state survives.
        effect_store_after_restart = PostgresToolEffectStore(self.connect)
        audit_after_restart = _Audit()

        async def forbidden_second_handler(_arguments):
            external_dispatches.append({"unexpected": "duplicate"})
            raise AssertionError("confirmed external side effect must not be dispatched again")

        gateway_after_restart = make_gateway(
            effect_store_after_restart, audit_after_restart, forbidden_second_handler,
        )
        replayed_result = await gateway_after_restart.call(**request)

        self.assertEqual(replayed_result, first_result)
        self.assertEqual(len(external_dispatches), 1)
        effect = await effect_store_after_restart.get(
            tenant_id=self.tenant_id, run_id=run_id, call_id=call_id,
        )
        self.assertEqual(effect.state, "succeeded")
        self.assertEqual(effect.result, first_result)
        self.assertEqual(audit_after_restart.records[-1]["outcome"], "replayed")


if __name__ == "__main__":
    unittest.main()
