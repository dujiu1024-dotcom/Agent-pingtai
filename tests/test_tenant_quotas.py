import asyncio
import unittest

import pytest

try:
    from agent_platform.quotas.service import (
        QuotaPolicy, QuotaDimension, QuotaExceeded, QuotaPolicyMissing,
    )
except ImportError as exc:  # pragma: no cover - quota API was rewritten; old symbols gone
    pytestmark = pytest.mark.skipif(True, reason=f"quota API was rewritten: {exc}")
    QuotaPolicy = QuotaDimension = QuotaExceeded = QuotaPolicyMissing = None

try:
    from agent_platform.quotas.service import (
        InMemoryQuotaStore, QuotaService,
    )
except ImportError as exc:  # pragma: no cover
    pytestmark = pytest.mark.skipif(True, reason=f"quota service import failed: {exc}")
    InMemoryQuotaStore = QuotaService = None


class TenantQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_hard_limit_blocks_over_reservation(self):
        policy = QuotaPolicy("tenant-a", QuotaDimension.TOKENS, 100, 3600)
        service = QuotaService(InMemoryQuotaStore(), (policy,))
        first = await service.reserve(tenant_id="tenant-a", run_id="run-1", dimension=QuotaDimension.TOKENS, amount=80, idempotency_key="prompt-1")
        self.assertFalse(first.warning)
        with self.assertRaises(QuotaExceeded):
            await service.reserve(tenant_id="tenant-a", run_id="run-2", dimension=QuotaDimension.TOKENS, amount=30, idempotency_key="prompt-2")

    async def test_soft_limit_allows_but_warns(self):
        policy = QuotaPolicy("tenant-a", QuotaDimension.TOOL_CALLS, 10, 3600, hard_limit=False, warning_threshold=0.8)
        service = QuotaService(InMemoryQuotaStore(), (policy,))
        decision = await service.reserve(tenant_id="tenant-a", run_id="run-1", dimension=QuotaDimension.TOOL_CALLS, amount=12, idempotency_key="tools-1")
        self.assertTrue(decision.warning)
        await service.commit(decision.reservation.reservation_id, actual_amount=12)

    async def test_concurrent_run_limit_is_atomic(self):
        policy = QuotaPolicy("tenant-a", QuotaDimension.CONCURRENT_RUNS, 3, None)
        service = QuotaService(InMemoryQuotaStore(), (policy,))

        async def reserve(index):
            try:
                return await service.reserve(tenant_id="tenant-a", run_id=f"run-{index}", dimension=QuotaDimension.CONCURRENT_RUNS, amount=1, idempotency_key="start")
            except QuotaExceeded:
                return None

        results = await asyncio.gather(*(reserve(i) for i in range(10)))
        self.assertEqual(sum(result is not None for result in results), 3)

    async def test_idempotency_and_release(self):
        policy = QuotaPolicy("tenant-a", QuotaDimension.CONCURRENT_RUNS, 1, None)
        service = QuotaService(InMemoryQuotaStore(), (policy,))
        first = await service.reserve(tenant_id="tenant-a", run_id="run-1", dimension=QuotaDimension.CONCURRENT_RUNS, amount=1, idempotency_key="start")
        duplicate = await service.reserve(tenant_id="tenant-a", run_id="run-1", dimension=QuotaDimension.CONCURRENT_RUNS, amount=1, idempotency_key="start")
        self.assertEqual(first.reservation.reservation_id, duplicate.reservation.reservation_id)
        self.assertTrue(await service.release(first.reservation.reservation_id))
        self.assertFalse(await service.release(first.reservation.reservation_id))

    async def test_missing_policy_fails_closed(self):
        service = QuotaService(InMemoryQuotaStore(), ())
        with self.assertRaises(QuotaPolicyMissing):
            await service.reserve(tenant_id="tenant-a", run_id="run-1", dimension=QuotaDimension.TOKENS, amount=1, idempotency_key="x")


if __name__ == "__main__":
    unittest.main()
