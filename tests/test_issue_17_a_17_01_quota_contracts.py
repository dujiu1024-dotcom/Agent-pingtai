from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.quotas import create_quota_reporting_router
from agent_platform.identity.models import Principal
from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter
from agent_platform.quotas.policies import (
    InMemoryQuotaPolicyRepository,
    QuotaPolicy,
    QuotaPolicyService,
)
from agent_platform.quotas.pricing import (
    CatalogModelCostEstimator,
    ModelPricingCatalog,
    PricingCard,
)
from agent_platform.quotas.service import Budget, BudgetExceeded, InMemoryQuotaStore, QuotaService
from agent_platform.quotas.storage_meter import S3TenantStorageMeter


class Issue17A1701ContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_c1701_policy_version_is_monotonic_and_audited(self):
        repository = InMemoryQuotaPolicyRepository(defaults={"requests": 100})
        service = QuotaPolicyService(repository)
        first = QuotaPolicy(
            "tenant-policy", "tenant", "tenant-a", {"tokens": 500},
            tenant_id="tenant-a", version=1,
        )
        await service.configure(first, actor_id="admin-a", authorize=lambda _: True)
        with self.assertRaisesRegex(ValueError, "increase monotonically"):
            await service.configure(first, actor_id="admin-a", authorize=lambda _: True)

        second = QuotaPolicy(
            "tenant-policy", "tenant", "tenant-a", {"tokens": 400},
            tenant_id="tenant-a", version=2,
        )
        await service.configure(second, actor_id="admin-b", authorize=lambda _: True)
        resolved = await service.resolve(
            __import__("agent_platform.quotas.policies", fromlist=["QuotaPolicyContext"]).QuotaPolicyContext("tenant-a")
        )
        self.assertEqual(resolved.limits, {"tokens": 400, "requests": 100})
        self.assertEqual(resolved.sources["tokens"], "tenant-policy")
        self.assertEqual(len(repository.audit_events), 2)
        self.assertEqual(repository.audit_events[1].before["version"], 1)
        self.assertEqual(repository.audit_events[1].after["version"], 2)
        self.assertEqual(repository.audit_events[1].actor_id, "admin-b")

    async def test_c1702_parallel_reservations_are_atomic_and_slots_release(self):
        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="queue", scope_id="interactive",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(hours=1), hard_concurrency=1,
        ))
        limiter = QuotaConcurrencyLimiter(service)

        async def acquire():
            try:
                return await limiter.acquire(
                    tenant_id="tenant-a", run_id=str(uuid4()),
                    scope_type="queue", scope_id="interactive",
                )
            except BudgetExceeded:
                return None

        reservations = await asyncio.gather(acquire(), acquire(), acquire())
        granted = [reservation for reservation in reservations if reservation is not None]
        self.assertEqual(len(granted), 1)
        self.assertTrue(await limiter.release(granted[0]))
        replacement = await limiter.acquire(
            tenant_id="tenant-a", run_id=str(uuid4()),
            scope_type="queue", scope_id="interactive",
        )
        self.assertTrue(await limiter.release(replacement))

        with self.assertRaises(ValueError):
            QuotaConcurrencyLimiter(service, lease_seconds=14)
        with self.assertRaises(ValueError):
            QuotaConcurrencyLimiter(service, lease_seconds=15, heartbeat_interval_seconds=15)

    async def test_c1703_cost_adapter_rounds_up_and_rejects_conflicting_usage(self):
        catalog = ModelPricingCatalog({
            "model-a": PricingCard(
                "model-a", "price-v7", Decimal("0.000001"),
                Decimal("0.000003"),
            )
        })
        estimator = CatalogModelCostEstimator(catalog)
        micros = await estimator.estimate_micros(
            model_id="model-a",
            usage={"prompt_tokens": 1, "completion_tokens": 0},
        )
        self.assertEqual(micros, 1)
        self.assertEqual(estimator.price_card_version("model-a"), "price-v7")
        self.assertIsNone(await estimator.estimate_micros(
            model_id="unpriced-model", usage={"prompt_tokens": 1, "completion_tokens": 1},
        ))
        with self.assertRaisesRegex(ValueError, "conflict"):
            await estimator.estimate_micros(
                model_id="model-a",
                usage={"prompt_tokens": 1, "input_tokens": 2, "completion_tokens": 0},
            )

    async def test_c1703_storage_meter_rejects_malformed_pagination_contract(self):
        class Client:
            def __init__(self, response):
                self.response = response
                self.calls = 0

            async def list_objects_v2(self, **kwargs):
                self.calls += 1
                return self.response

        for response, message in (
            ({"Contents": [], "IsTruncated": True}, "continuation token"),
            ({"Contents": [], "IsTruncated": "yes"}, "boolean"),
            ({"Contents": "not-a-list", "IsTruncated": False}, "list"),
        ):
            client = Client(response)
            meter = S3TenantStorageMeter(client=client, bucket="usage", max_pages=2)
            with self.subTest(response=response), self.assertRaisesRegex(ValueError, message):
                await meter.measure(tenant_id="tenant-a")
            self.assertEqual(client.calls, 1)

    async def test_c1704_budget_api_fails_closed_when_audit_is_unavailable(self):
        store = InMemoryQuotaStore()
        service = QuotaService(store)

        def unavailable_audit(_event):
            raise RuntimeError("audit backend unavailable")

        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=store,
            quota_service=service,
            authorize_manage=lambda _principal: True,
            audit_writer=unavailable_audit,
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = SimpleNamespace(principal=Principal(
                user_id="admin-a", tenant_id="tenant-a", session_id="session-a",
                email="admin@example.test", display_name="Admin",
            ))
            return await call_next(request)

        client = TestClient(app)
        response = client.post("/api/platform/v1/quotas/budgets", json={
            "scope_type": "tenant",
            "scope_id": "tenant-a",
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "hard_tokens": 100,
        })
        self.assertEqual(response.status_code, 503)
        budget = await store.get_budget(
            "tenant-a", "tenant", "tenant-a", datetime.now(timezone.utc),
        )
        self.assertIsNone(budget)


if __name__ == "__main__":
    unittest.main()
