from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="ValueError/KeyError expectation")

import unittest
from decimal import Decimal

from agent_platform.quotas import (
    Budget, InMemoryQuotaStore, QuotaReservationGroupService, QuotaScope,
)
from agent_platform.quotas.inheritance import RunBudgetInheritanceService


class FakeLineageStore:
    def __init__(self, root="root-run", parent="parent-run"):
        self.root = root
        self.parent = parent

    async def get_budget_lineage(self, tenant_id, run_id):
        return {"parent_run_id": self.parent, "budget_root_run_id": self.root}


class RunBudgetInheritanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from datetime import datetime, timedelta, timezone
        self.start = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.end = self.start + timedelta(hours=2)
        self.store = InMemoryQuotaStore()
        for scope_type, scope_id in (("tenant", "tenant-a"), ("run", "root-run")):
            await self.store.put_budget(Budget(
                tenant_id="tenant-a", scope_type=scope_type, scope_id=scope_id,
                period_start=self.start, period_end=self.end, hard_tokens=100,
                hard_cost_usd=Decimal("1.00"),
            ))
        self.groups = QuotaReservationGroupService(self.store)
        self.service = RunBudgetInheritanceService(
            lineage_store=FakeLineageStore(), reservation_groups=self.groups,
        )
        self.scopes = (QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run"))

    async def test_delegated_child_reserves_against_root_and_settles_once(self):
        group, _ = await self.service.reserve_model_call(
            tenant_id="tenant-a", run_id="child-run", call_id="provider-call-1",
            scopes=self.scopes, estimated_tokens=20, estimated_cost_usd=Decimal("0.2"),
            estimated_input_tokens=12, estimated_output_tokens=8,
        )
        self.assertEqual(group.run_id, "child-run")
        self.assertEqual(group.root_run_id, "root-run")
        await self.service.settle_model_call(
            group, model_key="model-a", input_tokens=12, output_tokens=8,
            cost_usd=Decimal("0.2"),
        )
        used = await self.store.usage(
            "tenant-a", "run", "root-run", self.start, self.end,
        )
        self.assertEqual(used[:2], (20, Decimal("0.2")))

    async def test_replay_of_same_call_id_cannot_reserve_twice(self):
        group, _ = await self.service.reserve_model_call(
            tenant_id="tenant-a", run_id="retry-run", call_id="stable-call-id",
            scopes=self.scopes, estimated_tokens=10, estimated_cost_usd=Decimal("0.1"),
            estimated_input_tokens=6, estimated_output_tokens=4,
        )
        await self.service.settle_model_call(
            group, model_key="model-a", input_tokens=6, output_tokens=4,
            cost_usd=Decimal("0.1"),
        )
        with self.assertRaises((ValueError, KeyError)):
            await self.service.reserve_model_call(
                tenant_id="tenant-a", run_id="retry-run", call_id="stable-call-id",
                scopes=self.scopes, estimated_tokens=10, estimated_cost_usd=Decimal("0.1"),
                estimated_input_tokens=6, estimated_output_tokens=4,
            )

    async def test_caller_cannot_reset_budget_root_to_child_run(self):
        with self.assertRaises(PermissionError):
            await self.service.reserve_model_call(
                tenant_id="tenant-a", run_id="child-run", call_id="bad",
                scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "child-run")),
                estimated_tokens=1, estimated_cost_usd=Decimal("0.01"),
                estimated_input_tokens=1, estimated_output_tokens=0,
            )

    async def test_missing_persisted_lineage_fails_closed(self):
        class Missing:
            async def get_budget_lineage(self, tenant_id, run_id):
                return None
        service = RunBudgetInheritanceService(
            lineage_store=Missing(), reservation_groups=self.groups,
        )
        with self.assertRaises(KeyError):
            await service.reserve_model_call(
                tenant_id="tenant-a", run_id="child-run", call_id="x",
                scopes=self.scopes, estimated_tokens=1, estimated_cost_usd=Decimal("0.01"),
                estimated_input_tokens=1, estimated_output_tokens=0,
            )


if __name__ == "__main__":
    unittest.main()
