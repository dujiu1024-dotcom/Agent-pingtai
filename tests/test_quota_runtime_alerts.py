from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from agent_platform.quotas.harness import QuotaEnforcedHarnessAdapter
from agent_platform.quotas.service import Budget, InMemoryQuotaStore, QuotaService


class RecordingHarness:
    def __init__(self):
        self.calls = 0

    async def run(self, _request):
        self.calls += 1
        return SimpleNamespace(usage={"input_tokens": 10, "output_tokens": 5}, tool_calls=())


class QuotaRuntimeAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.store = InMemoryQuotaStore()
        self.service = QuotaService(self.store)

    async def configure_soft_budget(self):
        await self.service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=self.now - timedelta(hours=1),
            period_end=self.now + timedelta(hours=1),
            hard_tokens=1000, soft_tokens=50,
        ))

    def request(self):
        return SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-alert",
            model_template=SimpleNamespace(template_id="model", version=1),
            project_id=None, agent_id="agent-a",
        ))

    async def test_soft_limit_emits_alert_before_runtime_dispatch(self):
        await self.configure_soft_budget()
        inner = RecordingHarness()
        alerts = []
        async def alert(event):
            alerts.append(event)
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=self.service,
            estimate_request=lambda _request: (100, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.01"),
            on_quota_warning=alert,
        )
        await adapter.run(self.request())
        self.assertEqual(inner.calls, 1)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["tenant_id"], "tenant-a")
        self.assertEqual(alerts[0]["run_id"], "run-alert")
        self.assertTrue(alerts[0]["warnings"])

    async def test_missing_alert_sink_fails_closed_before_dispatch(self):
        await self.configure_soft_budget()
        inner = RecordingHarness()
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=self.service,
            estimate_request=lambda _request: (100, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.01"),
        )
        with self.assertRaisesRegex(RuntimeError, "warning sink is not configured"):
            await adapter.run(self.request())
        self.assertEqual(inner.calls, 0)
        tokens, cost, concurrency = await self.store.usage(
            "tenant-a", "tenant", "tenant-a",
            self.now - timedelta(hours=1), self.now + timedelta(hours=1),
        )
        self.assertEqual((tokens, cost, concurrency), (0, Decimal("0"), 0))

    async def test_alert_delivery_failure_fails_closed_before_dispatch(self):
        await self.configure_soft_budget()
        inner = RecordingHarness()
        async def broken_alert(_event):
            raise RuntimeError("alert backend unavailable")
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=self.service,
            estimate_request=lambda _request: (100, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.01"),
            on_quota_warning=broken_alert,
        )
        with self.assertRaisesRegex(RuntimeError, "alert backend unavailable"):
            await adapter.run(self.request())
        self.assertEqual(inner.calls, 0)
        tokens, cost, concurrency = await self.store.usage(
            "tenant-a", "tenant", "tenant-a",
            self.now - timedelta(hours=1), self.now + timedelta(hours=1),
        )
        self.assertEqual((tokens, cost, concurrency), (0, Decimal("0"), 0))


if __name__ == "__main__":
    unittest.main()
