from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agent_platform.quotas import (
    Budget, BudgetExceeded, InMemoryQuotaStore, QuotaReservationGroupService,
    QuotaScope,
)


class QuotaReservationGroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.start = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.end = self.start + timedelta(hours=2)
        self.store = InMemoryQuotaStore()
        self.service = QuotaReservationGroupService(self.store)
        await self.store.put_budget(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=self.start, period_end=self.end, hard_tokens=100,
            hard_cost_usd=Decimal("2.00"),
        ))
        await self.store.put_budget(Budget(
            tenant_id="tenant-a", scope_type="run", scope_id="root-run",
            period_start=self.start, period_end=self.end, hard_tokens=100,
            hard_cost_usd=Decimal("2.00"),
        ))

    async def test_tenant_and_root_run_reserve_atomically_and_settle_one_event(self):
        group, warnings = await self.service.reserve_run(
            tenant_id="tenant-a", run_id="child-run", root_run_id="root-run",
            scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run")),
            estimated_tokens=80, estimated_cost_usd=Decimal("0.80"),
            estimated_input_tokens=60, estimated_output_tokens=20,
        )
        self.assertEqual(len(group.reservations), 2)
        self.assertEqual(warnings, ())
        event = await self.service.settle_run(
            group, model_key="model-x", input_tokens=30, output_tokens=20,
            cost_usd=Decimal("0.25"),
        )
        self.assertEqual(event.root_run_id, "root-run")
        self.assertEqual(len(await self.store.list_usage("tenant-a", self.start, self.end)), 1)
        for scope in (("tenant", "tenant-a"), ("run", "root-run")):
            tokens, cost, concurrency = await self.store.usage(
                "tenant-a", scope[0], scope[1], self.start, self.end,
            )
            self.assertEqual((tokens, cost, concurrency), (50, Decimal("0.25"), 0))

    async def test_failure_in_any_scope_leaves_no_partial_reservation(self):
        await self.store.put_budget(Budget(
            tenant_id="tenant-a", scope_type="run", scope_id="root-tight",
            period_start=self.start, period_end=self.end, hard_tokens=10,
        ))
        with self.assertRaises(BudgetExceeded):
            await self.service.reserve_run(
                tenant_id="tenant-a", run_id="child-run", root_run_id="root-tight",
                scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-tight")),
                estimated_tokens=20, estimated_cost_usd=Decimal("0.10"),
            )
        tokens, cost, concurrency = await self.store.usage(
            "tenant-a", "tenant", "tenant-a", self.start, self.end,
        )
        self.assertEqual((tokens, cost, concurrency), (0, Decimal("0"), 0))

    async def test_release_removes_every_scope_reservation(self):
        group, _ = await self.service.reserve_run(
            tenant_id="tenant-a", run_id="child-run", root_run_id="root-run",
            scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run")),
            estimated_tokens=40, estimated_cost_usd=Decimal("0.40"),
        )
        self.assertTrue(await self.service.release(group))
        self.assertFalse(await self.service.release(group))
        for scope in (("tenant", "tenant-a"), ("run", "root-run")):
            self.assertEqual(
                await self.store.usage("tenant-a", scope[0], scope[1], self.start, self.end),
                (0, Decimal("0"), 0),
            )


    async def test_concurrent_groups_cannot_overbook_shared_tenant_or_root_budget(self):
        async def reserve(run_id):
            try:
                return await self.service.reserve_run(
                    tenant_id="tenant-a", run_id=run_id, root_run_id="root-run",
                    scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run")),
                    estimated_tokens=60, estimated_cost_usd=Decimal("0.60"),
                )
            except BudgetExceeded:
                return None

        results = await asyncio.gather(reserve("child-a"), reserve("child-b"))
        accepted = [item for item in results if item is not None]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(
            await self.store.usage("tenant-a", "tenant", "tenant-a", self.start, self.end),
            (60, Decimal("0.60"), 1),
        )
        await self.service.release(accepted[0][0])

    async def test_idempotent_reservation_replay_does_not_double_reserve(self):
        kwargs = dict(
            tenant_id="tenant-a", run_id="child-retry", root_run_id="root-run",
            scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run")),
            estimated_tokens=60, estimated_cost_usd=Decimal("0.60"),
            estimated_input_tokens=40, estimated_output_tokens=20,
            idempotency_key="provider-attempt-1",
        )
        first, _ = await self.service.reserve_run(**kwargs)
        replay, _ = await self.service.reserve_run(**kwargs)
        self.assertEqual(first.group_id, replay.group_id)
        self.assertEqual(
            await self.store.usage("tenant-a", "tenant", "tenant-a", self.start, self.end),
            (60, Decimal("0.60"), 1),
        )
        with self.assertRaisesRegex(ValueError, "different reservation parameters"):
            await self.service.reserve_run(**{**kwargs, "estimated_tokens": 50,
                "estimated_input_tokens": 30, "estimated_output_tokens": 20})

    async def test_settlement_replay_is_idempotent_and_usage_conflicts_are_rejected(self):
        group, _ = await self.service.reserve_run(
            tenant_id="tenant-a", run_id="child-settle", root_run_id="root-run",
            scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run")),
            estimated_tokens=60, estimated_cost_usd=Decimal("0.60"),
            idempotency_key="provider-attempt-settle",
        )
        first = await self.service.settle_run(
            group, model_key="model-x", input_tokens=25, output_tokens=10,
            cost_usd=Decimal("0.20"),
        )
        replay = await self.service.settle_run(
            group, model_key="model-x", input_tokens=25, output_tokens=10,
            cost_usd=Decimal("0.20"),
        )
        self.assertEqual(first.event_id, replay.event_id)
        self.assertEqual(len(await self.store.list_usage("tenant-a", self.start, self.end)), 1)
        with self.assertRaisesRegex(ValueError, "different usage"):
            await self.service.settle_run(
                group, model_key="model-x", input_tokens=26, output_tokens=10,
                cost_usd=Decimal("0.20"),
            )

    async def test_release_then_new_retry_attempt_gets_fresh_reservation(self):
        first, _ = await self.service.reserve_run(
            tenant_id="tenant-a", run_id="child-retry-2", root_run_id="root-run",
            scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run")),
            estimated_tokens=70, estimated_cost_usd=Decimal("0.70"),
            idempotency_key="attempt-1",
        )
        self.assertTrue(await self.service.release(first))
        second, _ = await self.service.reserve_run(
            tenant_id="tenant-a", run_id="child-retry-2", root_run_id="root-run",
            scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run")),
            estimated_tokens=70, estimated_cost_usd=Decimal("0.70"),
            idempotency_key="attempt-2",
        )
        self.assertNotEqual(first.group_id, second.group_id)
        self.assertEqual(
            await self.store.usage("tenant-a", "tenant", "tenant-a", self.start, self.end),
            (70, Decimal("0.70"), 1),
        )

    async def test_duplicate_scopes_and_wrong_root_scope_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            await self.service.reserve_run(
                tenant_id="tenant-a", run_id="child-run", root_run_id="root-run",
                scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("tenant", "tenant-a")),
                estimated_tokens=1, estimated_cost_usd=Decimal("0.01"),
            )
        with self.assertRaisesRegex(ValueError, "must include the root Run budget"):
            await self.service.reserve_run(
                tenant_id="tenant-a", run_id="child-run", root_run_id="root-run",
                scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "other-root")),
                estimated_tokens=1, estimated_cost_usd=Decimal("0.01"),
            )


if __name__ == "__main__":
    unittest.main()
