"""PostgreSQL integration coverage for atomic multi-scope quota groups."""

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


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class QuotaReservationGroupPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def _seed(self, tenant_id, root_run_id, child_run_id, tight_root_id=None):
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(migration.read_text(), prepare=False)
            agent_id, template_id = f"agent-{uuid4().hex}", f"template-{uuid4().hex}"
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_id, "Quota Group Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id, agent_id, display_name, role_definition)
                   VALUES (%s, %s, 'Quota Agent', 'integration')""",
                (tenant_id, agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_model_templates
                   (tenant_id, template_id, version, definition, status, created_by)
                   VALUES (%s, %s, 1, '{}'::jsonb, 'active', 'integration')""",
                (tenant_id, template_id),
            )
            for run_id in (root_run_id, child_run_id, tight_root_id):
                if run_id is None:
                    continue
                await conn.execute(
                    """INSERT INTO platform_runs
                       (tenant_id, run_id, agent_id, template_id, template_version, status)
                       VALUES (%s, %s, %s, %s, 1, 'queued')""",
                    (tenant_id, run_id, agent_id, template_id),
                )
        return agent_id

    async def test_tenant_and_root_run_reservations_commit_and_settle_one_ledger_event(self):
        from agent_platform.quotas import (
            Budget, PostgresQuotaStore, QuotaReservationGroupService, QuotaScope,
        )

        tenant_id = f"quota-group-it-{uuid4().hex}"
        root_run_id, child_run_id = uuid4(), uuid4()
        await self._seed(tenant_id, root_run_id, child_run_id)
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = start + timedelta(hours=1)
        store = PostgresQuotaStore.from_dsn(DSN)
        service = QuotaReservationGroupService(store)
        for scope_type, scope_id in (("tenant", tenant_id), ("run", str(root_run_id))):
            await store.put_budget(Budget(
                tenant_id=tenant_id, scope_type=scope_type, scope_id=scope_id,
                period_start=start, period_end=end, hard_tokens=100,
                hard_cost_usd=Decimal("2.00"),
            ))
        group, _ = await service.reserve_run(
            tenant_id=tenant_id, run_id=str(child_run_id), root_run_id=str(root_run_id),
            scopes=(QuotaScope("tenant", tenant_id), QuotaScope("run", str(root_run_id))),
            estimated_tokens=80, estimated_cost_usd=Decimal("0.80"),
            estimated_input_tokens=60, estimated_output_tokens=20,
        )
        event = await service.settle_run(
            group, model_key="model-group-it", input_tokens=30, output_tokens=20,
            cost_usd=Decimal("0.25"),
        )
        self.assertEqual(event.root_run_id, str(root_run_id))
        rows = await store.list_usage(tenant_id, start, end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].total_tokens, 50)
        for scope_type, scope_id in (("tenant", tenant_id), ("run", str(root_run_id))):
            self.assertEqual(
                await store.usage(tenant_id, scope_type, scope_id, start, end),
                (50, Decimal("0.25"), 0),
            )

    async def test_idempotent_reservation_and_settlement_replay_do_not_double_charge(self):
        from agent_platform.quotas import (
            Budget, PostgresQuotaStore, QuotaReservationGroupService, QuotaScope,
        )

        tenant_id = f"quota-group-replay-it-{uuid4().hex}"
        root_run_id, child_run_id = uuid4(), uuid4()
        await self._seed(tenant_id, root_run_id, child_run_id)
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = start + timedelta(hours=1)
        store = PostgresQuotaStore.from_dsn(DSN)
        service = QuotaReservationGroupService(store)
        for scope_type, scope_id in (("tenant", tenant_id), ("run", str(root_run_id))):
            await store.put_budget(Budget(
                tenant_id=tenant_id, scope_type=scope_type, scope_id=scope_id,
                period_start=start, period_end=end, hard_tokens=100,
                hard_cost_usd=Decimal("2.00"),
            ))
        kwargs = dict(
            tenant_id=tenant_id, run_id=str(child_run_id), root_run_id=str(root_run_id),
            scopes=(QuotaScope("tenant", tenant_id), QuotaScope("run", str(root_run_id))),
            estimated_tokens=80, estimated_cost_usd=Decimal("0.80"),
            estimated_input_tokens=60, estimated_output_tokens=20,
            idempotency_key="provider-attempt-1",
        )
        first_result, second_result = await asyncio.gather(
            service.reserve_run(**kwargs), service.reserve_run(**kwargs),
        )
        group, replay = first_result[0], second_result[0]
        self.assertEqual(group.group_id, replay.group_id)
        self.assertEqual(
            await store.usage(tenant_id, "tenant", tenant_id, start, end),
            (80, Decimal("0.80"), 1),
        )
        first = await service.settle_run(
            group, model_key="model-group-replay", input_tokens=30, output_tokens=20,
            cost_usd=Decimal("0.25"),
        )
        second = await service.settle_run(
            replay, model_key="model-group-replay", input_tokens=30, output_tokens=20,
            cost_usd=Decimal("0.25"),
        )
        self.assertEqual(first.event_id, second.event_id)
        rows = await store.list_usage(tenant_id, start, end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].total_tokens, 50)
        with self.assertRaisesRegex(ValueError, "different usage"):
            await service.settle_run(
                replay, model_key="model-group-replay", input_tokens=31, output_tokens=20,
                cost_usd=Decimal("0.25"),
            )

    async def test_concurrent_distinct_attempts_cannot_overdraw_shared_budgets(self):
        from agent_platform.quotas import (
            Budget, BudgetExceeded, PostgresQuotaStore,
            QuotaReservationGroupService, QuotaScope,
        )

        tenant_id = f"quota-group-concurrency-it-{uuid4().hex}"
        root_run_id, child_a, child_b = uuid4(), uuid4(), uuid4()
        await self._seed(tenant_id, root_run_id, child_a)
        # Seed the second child Run under the same tenant and Agent.
        async with await AsyncConnection.connect(DSN) as conn:
            cursor = await conn.execute(
                "SELECT agent_id, template_id FROM platform_runs WHERE tenant_id=%s AND run_id=%s",
                (tenant_id, child_a),
            )
            row = await cursor.fetchone()
            await conn.execute(
                """INSERT INTO platform_runs
                   (tenant_id, run_id, agent_id, template_id, template_version, status)
                   VALUES (%s, %s, %s, %s, 1, 'queued')""",
                (tenant_id, child_b, row["agent_id"], row["template_id"]),
            )
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = start + timedelta(hours=1)
        store = PostgresQuotaStore.from_dsn(DSN)
        service = QuotaReservationGroupService(store)
        for scope_type, scope_id in (("tenant", tenant_id), ("run", str(root_run_id))):
            await store.put_budget(Budget(
                tenant_id=tenant_id, scope_type=scope_type, scope_id=scope_id,
                period_start=start, period_end=end, hard_tokens=100,
                hard_cost_usd=Decimal("2.00"),
            ))

        async def reserve(run_id, attempt):
            try:
                return await service.reserve_run(
                    tenant_id=tenant_id, run_id=str(run_id), root_run_id=str(root_run_id),
                    scopes=(QuotaScope("tenant", tenant_id), QuotaScope("run", str(root_run_id))),
                    estimated_tokens=60, estimated_cost_usd=Decimal("0.60"),
                    idempotency_key=attempt,
                )
            except BudgetExceeded:
                return None

        results = await asyncio.gather(
            reserve(child_a, "attempt-a"), reserve(child_b, "attempt-b"),
        )
        accepted = [result for result in results if result is not None]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(
            await store.usage(tenant_id, "tenant", tenant_id, start, end),
            (60, Decimal("0.60"), 1),
        )
        self.assertEqual(
            await store.usage(tenant_id, "run", str(root_run_id), start, end),
            (60, Decimal("0.60"), 1),
        )
        await service.release(accepted[0][0])

    async def test_second_scope_failure_rolls_back_first_scope_reservation(self):
        from agent_platform.quotas import (
            Budget, BudgetExceeded, PostgresQuotaStore,
            QuotaReservationGroupService, QuotaScope,
        )

        tenant_id = f"quota-group-rollback-it-{uuid4().hex}"
        root_run_id, child_run_id = uuid4(), uuid4()
        await self._seed(tenant_id, root_run_id, child_run_id)
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = start + timedelta(hours=1)
        store = PostgresQuotaStore.from_dsn(DSN)
        service = QuotaReservationGroupService(store)
        await store.put_budget(Budget(
            tenant_id=tenant_id, scope_type="tenant", scope_id=tenant_id,
            period_start=start, period_end=end, hard_tokens=10,
        ))
        await store.put_budget(Budget(
            tenant_id=tenant_id, scope_type="run", scope_id=str(root_run_id),
            period_start=start, period_end=end, hard_tokens=100,
        ))
        with self.assertRaises(BudgetExceeded):
            await service.reserve_run(
                tenant_id=tenant_id, run_id=str(child_run_id), root_run_id=str(root_run_id),
                scopes=(QuotaScope("tenant", tenant_id), QuotaScope("run", str(root_run_id))),
                estimated_tokens=20, estimated_cost_usd=Decimal("0.10"),
            )
        self.assertEqual(
            await store.usage(tenant_id, "tenant", tenant_id, start, end),
            (0, Decimal("0"), 0),
        )
        self.assertEqual(
            await store.usage(tenant_id, "run", str(root_run_id), start, end),
            (0, Decimal("0"), 0),
        )
