from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.quota_admin import create_quota_admin_router
from agent_platform.identity.models import Principal
from agent_platform.quotas.service import InMemoryQuotaStore, QuotaService


class QuotaAdminApiTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryQuotaStore()
        self.service = QuotaService(self.store)
        self.events = []
        app = FastAPI()
        app.include_router(create_quota_admin_router(
            service=self.service, authorize_manage=lambda actor: actor.user_id == "admin",
            authorize_scope=lambda actor, kind, ident: ident == "project-1",
            audit_writer=self.events.append,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="admin", tenant_id="tenant-a", session_id="s",
                    email="admin@example.test", display_name="Admin",
                )
            })()
            return await call_next(request)

        self.client = TestClient(app)
        self.start = datetime.now(timezone.utc) - timedelta(hours=1)
        self.end = self.start + timedelta(days=1)

    def test_put_budget_is_audited_and_readable_with_usage(self):
        body = {
            "period_start": self.start.isoformat(), "period_end": self.end.isoformat(),
            "hard_tokens": 500, "hard_cost_usd": "2.50", "hard_concurrency": 3,
            "soft_tokens": 400,
        }
        response = self.client.put(
            "/api/platform/v1/quotas/budgets/tenant/tenant-a", json=body,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["action"], "quota.budget.updated")
        self.assertEqual(self.events[0]["tenant_id"], "tenant-a")
        fetched = self.client.get("/api/platform/v1/quotas/budgets/tenant/tenant-a")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["hard_tokens"], 500)
        usage = self.client.get("/api/platform/v1/quotas/usage/current/tenant/tenant-a")
        self.assertEqual(usage.status_code, 200)
        self.assertEqual(usage.json()["used_tokens"], 0)
        self.assertEqual(usage.json()["hard_concurrency"], 3)

    def test_cross_tenant_scope_and_unauthorized_mutation_are_denied(self):
        response = self.client.put(
            "/api/platform/v1/quotas/budgets/tenant/tenant-b",
            json={"period_start": self.start.isoformat(), "period_end": self.end.isoformat(),
                  "hard_tokens": 10},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.events, [])

    def test_resource_scope_requires_explicit_authorization(self):
        response = self.client.put(
            "/api/platform/v1/quotas/budgets/project/project-other",
            json={"period_start": self.start.isoformat(), "period_end": self.end.isoformat(),
                  "hard_tokens": 10},
        )
        self.assertEqual(response.status_code, 403)

    def test_audit_failure_prevents_budget_change(self):
        def broken_audit(_event):
            raise RuntimeError("audit unavailable")
        app = FastAPI()
        app.include_router(create_quota_admin_router(
            service=self.service, authorize_manage=lambda actor: True,
            audit_writer=broken_audit,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="admin", tenant_id="tenant-a", session_id="s",
                    email="admin@example.test", display_name="Admin",
                )
            })()
            return await call_next(request)

        client = TestClient(app)
        response = client.put(
            "/api/platform/v1/quotas/budgets/tenant/tenant-a",
            json={"period_start": self.start.isoformat(), "period_end": self.end.isoformat(),
                  "hard_tokens": 10},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(asyncio.run(self.store.get_budget(
            "tenant-a", "tenant", "tenant-a", datetime.now(timezone.utc),
        )))

    def test_invalid_limits_are_rejected(self):
        response = self.client.put(
            "/api/platform/v1/quotas/budgets/tenant/tenant-a",
            json={"period_start": self.start.isoformat(), "period_end": self.end.isoformat(),
                  "hard_tokens": True},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
