from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from agent_platform.quotas import Budget, BudgetExceeded, PostgresQuotaStore, QuotaReservationGroupService, QuotaScope, RunBudgetInheritanceService
    from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
    from agent_platform.quotas.run_ledger import PostgresRunBudgetLedger


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class RunBudgetGovernancePostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"budget-it-{uuid4().hex}"
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(migration.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "Run budget integration"),
            )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.run_store = PostgresRunStateStore(connect)
        self.quota_store = PostgresQuotaStore(connect)
        self.groups = QuotaReservationGroupService(self.quota_store)
        self.start = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.end = self.start + timedelta(hours=1)
        self.root_id = str(uuid4())
        await self._create_run(self.root_id)
        self.inheritance = RunBudgetInheritanceService(
            lineage_store=self.run_store, reservation_groups=self.groups,
        )
        self.run_budget_ledger = PostgresRunBudgetLedger(connect)

    async def _create_run(self, run_id, *, parent_run_id=None, lineage_key=None):
        await self.run_store.create_task_run(
            tenant_id=self.tenant_id, run_id=run_id, actor_id="integration-owner",
            agent_id="agent-budget-test", agent_version=1, template_id="model-template",
            template_version=1, input={"prompt": "integration"},
            preparation_owner="budget-test-worker",
            registry_snapshot=[{"kind": "agent", "id": "agent-budget-test", "version": 1}],
            parent_run_id=parent_run_id, budget_lineage_key=lineage_key,
        )

    async def _child(self, key):
        run_id = str(uuid4())
        await self._create_run(run_id, parent_run_id=self.root_id, lineage_key=key)
        return run_id

    async def _configure(self, *, tenant_tokens=100, root_tokens=100, root_concurrency=None):
        for kind, scope_id, limit, concurrency in (
            ("tenant", self.tenant_id, tenant_tokens, None),
            ("run", self.root_id, root_tokens, root_concurrency),
        ):
            await self.quota_store.put_budget(Budget(
                tenant_id=self.tenant_id, scope_type=kind, scope_id=scope_id,
                period_start=self.start, period_end=self.end,
                hard_tokens=limit, hard_cost_usd=Decimal("10.00"),
                hard_concurrency=concurrency,
            ))

    async def _reserve(self, run_id, call_id, tokens):
        return await self.inheritance.reserve_model_call(
            tenant_id=self.tenant_id, run_id=run_id, call_id=call_id,
            scopes=(QuotaScope("tenant", self.tenant_id), QuotaScope("run", self.root_id)),
            estimated_tokens=tokens, estimated_cost_usd=Decimal("0.10"),
            estimated_input_tokens=tokens, estimated_output_tokens=0,
        )

    async def test_model_call_budget_estimate_actual_and_variance_are_queryable(self):
        await self.run_budget_ledger.configure(
            tenant_id=self.tenant_id, run_id=self.root_id,
            max_tokens=100, max_cost_usd=Decimal("1.00"),
        )
        call_id = str(uuid4())
        await self.run_budget_ledger.reserve(
            call_id=call_id, tenant_id=self.tenant_id, run_id=self.root_id,
            model_key="integration-model", estimated_input_tokens=40,
            estimated_output_tokens=20, estimated_cost_usd=Decimal("0.60"),
        )
        await self.run_budget_ledger.settle(
            call_id=call_id, actual_input_tokens=30,
            actual_output_tokens=15, actual_cost_usd=Decimal("0.45"),
        )
        snapshot = await self.run_budget_ledger.snapshot(self.tenant_id, self.root_id)
        self.assertEqual(snapshot.actual_tokens, 45)
        self.assertEqual(snapshot.remaining_tokens, 55)
        items = await self.run_budget_ledger.list_model_calls(
            tenant_id=self.tenant_id, run_id=self.root_id,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["estimated_total_tokens"], 60)
        self.assertEqual(items[0]["actual_total_tokens"], 45)
        self.assertEqual(items[0]["token_delta"], -15)
        self.assertEqual(items[0]["estimated_cost_usd"], "0.60000000")
        self.assertEqual(items[0]["actual_cost_usd"], "0.45000000")
        self.assertEqual(items[0]["cost_delta_usd"], "-0.15000000")

    async def test_persisted_child_lineage_and_transaction_rollback(self):
        child = await self._child("delegation:rollback")
        lineage = await self.run_store.get_budget_lineage(self.tenant_id, child)
        self.assertEqual(lineage["budget_root_run_id"], self.root_id)
        self.assertEqual(lineage["parent_run_id"], self.root_id)
        # The Run scope is locked/reserved first; tenant scope fails second.
        # The transaction must roll back the already inserted Run reservation.
        await self._configure(tenant_tokens=10, root_tokens=100)
        with self.assertRaises(BudgetExceeded):
            await self._reserve(child, "rollback-call", 11)
        for kind, scope_id in (("run", self.root_id), ("tenant", self.tenant_id)):
            self.assertEqual(
                await self.quota_store.usage(self.tenant_id, kind, scope_id, self.start, self.end),
                (0, Decimal("0"), 0),
            )

    async def test_parallel_child_runs_cannot_overbook_root_budget(self):
        first, second = await self._child("delegation:first"), await self._child("delegation:second")
        await self._configure(tenant_tokens=100, root_tokens=20, root_concurrency=1)

        async def attempt(run_id, call_id):
            try:
                return await self._reserve(run_id, call_id, 12)
            except BudgetExceeded:
                return None

        results = await asyncio.gather(
            attempt(first, "parallel-call-1"), attempt(second, "parallel-call-2"),
        )
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(
            (await self.quota_store.usage(
                self.tenant_id, "run", self.root_id, self.start, self.end,
            ))[2],
            1,
        )

    async def test_provider_failure_release_then_retry_and_settle_once(self):
        child = await self._child("delegation:provider-failure")
        await self._configure()
        failed, _ = await self._reserve(child, "provider-attempt-1", 10)
        self.assertTrue(await self.groups.release(failed))
        self.assertEqual(
            (await self.quota_store.usage(
                self.tenant_id, "run", self.root_id, self.start, self.end,
            ))[2],
            0,
        )
        retry, _ = await self._reserve(child, "provider-attempt-2", 10)
        self.assertEqual(retry.root_run_id, self.root_id)
        await self.inheritance.settle_model_call(
            retry, model_key="test-model", input_tokens=8, output_tokens=2,
            cost_usd=Decimal("0.10"),
        )
        with self.assertRaises(KeyError):
            await self.inheritance.settle_model_call(
                retry, model_key="test-model", input_tokens=8, output_tokens=2,
                cost_usd=Decimal("0.10"),
            )
        self.assertEqual(
            (await self.quota_store.usage(
                self.tenant_id, "run", self.root_id, self.start, self.end,
            ))[:2],
            (10, Decimal("0.10")),
        )


if __name__ == "__main__":
    unittest.main()
