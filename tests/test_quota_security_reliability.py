"""Security and reliability regression tests for tenant quota operations.

PostgreSQL-specific isolation coverage is opt-in via AGENT_PLATFORM_TEST_DSN;
unit/API tests run without external services.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.identity.models import Principal
from agent_platform.quotas.postgres import PostgresQuotaStore
from agent_platform.quotas.service import Budget, InMemoryQuotaStore, QuotaService

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


def _principal(tenant_id: str = "tenant-a") -> Principal:
    return Principal(
        user_id="operator-a", tenant_id=tenant_id, session_id="session-a",
        email="operator@example.test", display_name="Operator",
    )


def _app(*, store, service=None, principal=None, authorize_manage=None,
         authorize_scope=None, audit_writer=None, authenticated=True):
    app = FastAPI()
    from agent_platform.api.quotas import create_quota_reporting_router

    app.include_router(create_quota_reporting_router(
        quota_store=store,
        quota_service=service,
        authorize_manage=authorize_manage,
        authorize_scope=authorize_scope,
        audit_writer=audit_writer,
    ))

    @app.middleware("http")
    async def identity(request, call_next):
        if authenticated:
            request.state.identity = type(
                "Identity", (), {"principal": principal or _principal()}
            )()
        return await call_next(request)

    return app


def _budget_body(scope_id="tenant-a"):
    return {
        "scope_type": "tenant",
        "scope_id": scope_id,
        "period_start": "2026-11-01T00:00:00+00:00",
        "period_end": "2026-12-01T00:00:00+00:00",
        "hard_tokens": 100,
        "hard_concurrency": 2,
    }


class QuotaSecurityReliabilityApiTests(unittest.TestCase):
    def test_unauthenticated_requests_are_rejected(self):
        app = _app(store=object(), authenticated=False)
        response = TestClient(app).get(
            "/api/platform/v1/quotas/usage",
            params={"start": "2026-11-01T00:00:00+00:00",
                    "end": "2026-11-02T00:00:00+00:00"},
        )
        self.assertEqual(response.status_code, 401)

    def test_management_permission_and_cross_tenant_scope_are_denied(self):
        service = QuotaService(InMemoryQuotaStore())
        app = _app(
            store=object(), service=service, audit_writer=lambda event: None,
            authorize_manage=lambda actor: False,
        )
        response = TestClient(app).post(
            "/api/platform/v1/quotas/budgets", json=_budget_body()
        )
        self.assertEqual(response.status_code, 403)

        app = _app(
            store=object(), service=service, audit_writer=lambda event: None,
            authorize_manage=lambda actor: True,
        )
        response = TestClient(app).post(
            "/api/platform/v1/quotas/budgets", json=_budget_body("tenant-b")
        )
        self.assertEqual(response.status_code, 403)

    def test_audit_dependency_failure_fails_closed_without_applying_budget(self):
        store = InMemoryQuotaStore()
        service = QuotaService(store)

        def unavailable_audit(_event):
            raise RuntimeError("audit database unavailable")

        app = _app(
            store=object(), service=service, audit_writer=unavailable_audit,
            authorize_manage=lambda actor: True,
        )
        response = TestClient(app).post(
            "/api/platform/v1/quotas/budgets", json=_budget_body()
        )
        self.assertEqual(response.status_code, 503)
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        self.assertIsNone(awaitable_result_placeholder) if False else None
        self.assertIsNone(
            _run(service.store.get_budget("tenant-a", "tenant", "tenant-a", now))
        )

    def test_usage_store_dependency_failure_is_not_reported_as_success(self):
        class UnavailableStore:
            async def list_usage(self, *args, **kwargs):
                raise ConnectionError("quota database unavailable")

        app = _app(store=UnavailableStore())
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/platform/v1/quotas/usage",
            params={"start": "2026-11-01T00:00:00+00:00",
                    "end": "2026-11-02T00:00:00+00:00"},
        )
        self.assertEqual(response.status_code, 500)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class QuotaSecurityReliabilityPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_postgres_budget_reads_are_tenant_isolated(self):
        from uuid import uuid4

        from psycopg import AsyncConnection

        tenant_a = f"quota-isolation-a-{uuid4().hex}"
        tenant_b = f"quota-isolation-b-{uuid4().hex}"
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=1)
        end = now + timedelta(hours=1)
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(migration.read_text(), prepare=False)
            for tenant in (tenant_a, tenant_b):
                await conn.execute(
                    "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                    (tenant, "Quota tenant isolation integration"),
                )

        store = PostgresQuotaStore.from_dsn(DSN)
        for tenant, hard_tokens in ((tenant_a, 11), (tenant_b, 29)):
            await store.put_budget(Budget(
                tenant_id=tenant, scope_type="tenant", scope_id=tenant,
                period_start=start, period_end=end, hard_tokens=hard_tokens,
            ))

        own = await store.get_budget(tenant_a, "tenant", tenant_a, now)
        other = await store.get_budget(tenant_b, "tenant", tenant_b, now)
        cross_scope = await store.get_budget(tenant_a, "tenant", tenant_b, now)
        self.assertEqual(own.hard_tokens, 11)
        self.assertEqual(other.hard_tokens, 29)
        self.assertIsNone(cross_scope)

    async def test_postgres_dependency_outage_is_propagated(self):
        async def unavailable_connection():
            raise ConnectionError("postgres unavailable")

        store = PostgresQuotaStore(unavailable_connection)
        with self.assertRaisesRegex(ConnectionError, "postgres unavailable"):
            await store.get_budget(
                "tenant-a", "tenant", "tenant-a", datetime.now(timezone.utc)
            )


if __name__ == "__main__":
    unittest.main()
