from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.quotas.policies import (
    InMemoryQuotaPolicyRepository,
    PostgresQuotaPolicyRepository,
    QuotaPolicy,
    QuotaPolicyAudit,
    QuotaPolicyContext,
    QuotaPolicyService,
)


class HierarchicalQuotaPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryQuotaPolicyRepository()
        self.service = QuotaPolicyService(self.repository)

    async def test_specificity_overrides_parent_and_merges_resource_dimensions(self):
        for policy in (
            QuotaPolicy("defaults", "default", "*", {"tokens": 1000, "requests": 100}),
            QuotaPolicy("tenant", "tenant", "t-1", {"tokens": 800, "cost_usd": 20}, tenant_id="t-1"),
            QuotaPolicy("project", "project", "p-1", {"tokens": 500, "storage_bytes": 2048}, tenant_id="t-1"),
            QuotaPolicy("subject", "subject", "u-1", {"concurrency": 2}, tenant_id="t-1"),
        ):
            await self.service.configure(policy, actor_id="admin", authorize=lambda _: True)
        resolved = await self.service.resolve(QuotaPolicyContext("t-1", "p-1", "u-1"))
        self.assertEqual(resolved.limits, {
            "tokens": 500, "cost_usd": 20, "storage_bytes": 2048,
            "concurrency": 2, "requests": 100,
        })
        self.assertEqual(resolved.sources["tokens"], "project")

    async def test_priority_breaks_ties_inside_same_scope(self):
        await self.service.configure(
            QuotaPolicy("low", "tenant", "t-1", {"tokens": 100}, priority=10, tenant_id="t-1"),
            actor_id="admin", authorize=lambda _: True,
        )
        await self.service.configure(
            QuotaPolicy("high", "tenant", "t-1", {"tokens": 50}, priority=20, tenant_id="t-1"),
            actor_id="admin", authorize=lambda _: True,
        )
        resolved = await self.service.resolve(QuotaPolicyContext("t-1"))
        self.assertEqual(resolved.limits["tokens"], 50)
        self.assertEqual(resolved.sources["tokens"], "high")

    async def test_tenant_isolation_and_disabled_policy(self):
        await self.service.configure(
            QuotaPolicy("foreign", "tenant", "t-2", {"tokens": 1}, tenant_id="t-2"),
            actor_id="admin", authorize=lambda _: True,
        )
        await self.service.configure(
            QuotaPolicy("disabled", "tenant", "t-1", {"tokens": 2}, enabled=False, tenant_id="t-1"),
            actor_id="admin", authorize=lambda _: True,
        )
        resolved = await self.service.resolve(QuotaPolicyContext("t-1"))
        self.assertNotIn("tokens", resolved.limits)

    async def test_configuration_requires_authorization_and_writes_audit(self):
        policy = QuotaPolicy("tenant", "tenant", "t-1", {"tokens": 10}, tenant_id="t-1")
        with self.assertRaises(PermissionError):
            await self.service.configure(policy, actor_id="user", authorize=lambda _: False)
        self.assertEqual(self.repository.audit_events, [])
        await self.service.configure(policy, actor_id="admin", authorize=lambda _: True)
        self.assertEqual(len(self.repository.audit_events), 1)
        event = self.repository.audit_events[0]
        self.assertEqual(event.action, "quota.policy.created")
        self.assertEqual(len(event.payload_sha256), 64)

    async def test_invalid_resource_dimension_and_nonfinite_values_rejected(self):
        with self.assertRaises(ValueError):
            QuotaPolicy("bad", "tenant", "t-1", {"memory": 10}, tenant_id="t-1")
        with self.assertRaises(ValueError):
            QuotaPolicy("bad", "tenant", "t-1", {"tokens": float("nan")}, tenant_id="t-1")


class _FakeCursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount


class _FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.snapshot = (
            list(self.connection.policy_rows), list(self.connection.audit_rows),
        )
        self.connection.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.connection.in_transaction = False
        if exc_type is not None:
            self.connection.policy_rows, self.connection.audit_rows = self.snapshot
        return False


class _FakePostgresConnection:
    def __init__(self, *, fail_audit=False, policy_rowcount=1):
        self.fail_audit = fail_audit
        self.policy_rowcount = policy_rowcount
        self.policy_rows = []
        self.audit_rows = []
        self.in_transaction = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return _FakeTransaction(self)

    async def execute(self, query, params):
        if not self.in_transaction:
            raise AssertionError("policy and audit writes must share a transaction")
        if "INSERT INTO platform_quota_policies" in query:
            self.policy_rows.append(params)
            return _FakeCursor(self.policy_rowcount)
        if "INSERT INTO platform_quota_policy_audit" in query:
            if self.fail_audit:
                raise RuntimeError("audit database unavailable")
            self.audit_rows.append(params)
            return _FakeCursor()
        raise AssertionError("unexpected SQL statement")


class PostgresQuotaPolicyAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_and_audit_are_written_in_one_transaction(self):
        connection = _FakePostgresConnection()
        repository = PostgresQuotaPolicyRepository(lambda: _async_value(connection))
        policy = QuotaPolicy(
            "tenant-policy", "tenant", "tenant-a", {"tokens": 100},
            tenant_id="tenant-a",
        )
        event = QuotaPolicyAudit(
            audit_id="audit-1", tenant_id="tenant-a", actor_id="admin",
            action="quota.policy.created", policy_id="tenant-policy",
            policy_version=1, before=None,
            after={"scope_type": "tenant", "scope_id": "tenant-a", "limits": {"tokens": 100}},
            occurred_at=datetime.now(timezone.utc), payload_sha256="a" * 64,
        )
        await repository.put_policy_and_audit(policy, event)
        self.assertEqual(len(connection.policy_rows), 1)
        self.assertEqual(len(connection.audit_rows), 1)

    async def test_audit_failure_rolls_back_policy_write(self):
        connection = _FakePostgresConnection(fail_audit=True)
        repository = PostgresQuotaPolicyRepository(lambda: _async_value(connection))
        policy = QuotaPolicy(
            "tenant-policy", "tenant", "tenant-a", {"tokens": 100},
            tenant_id="tenant-a",
        )
        event = QuotaPolicyAudit(
            audit_id="audit-2", tenant_id="tenant-a", actor_id="admin",
            action="quota.policy.created", policy_id="tenant-policy",
            policy_version=1, before=None, after={"limits": {"tokens": 100}},
            occurred_at=datetime.now(timezone.utc), payload_sha256="b" * 64,
        )
        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await repository.put_policy_and_audit(policy, event)
        self.assertEqual(connection.policy_rows, [])
        self.assertEqual(connection.audit_rows, [])

    async def test_stale_version_does_not_append_audit(self):
        connection = _FakePostgresConnection(policy_rowcount=0)
        repository = PostgresQuotaPolicyRepository(lambda: _async_value(connection))
        policy = QuotaPolicy(
            "tenant-policy", "tenant", "tenant-a", {"tokens": 100},
            tenant_id="tenant-a", version=2,
        )
        event = QuotaPolicyAudit(
            audit_id="audit-3", tenant_id="tenant-a", actor_id="admin",
            action="quota.policy.updated", policy_id="tenant-policy",
            policy_version=2, before={"version": 1}, after={"version": 2},
            occurred_at=datetime.now(timezone.utc), payload_sha256="c" * 64,
        )
        with self.assertRaisesRegex(ValueError, "stale or already exists"):
            await repository.put_policy_and_audit(policy, event)
        self.assertEqual(connection.audit_rows, [])


async def _async_value(value):
    return value


if __name__ == "__main__":
    unittest.main()
