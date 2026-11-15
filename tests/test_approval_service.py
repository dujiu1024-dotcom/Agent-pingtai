from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.approval.policy import RiskLevel
from agent_platform.approval.service import ApprovalRecord, ApprovalService


class MemoryApprovalRepository:
    def __init__(self):
        self.records = {}

    async def create(self, record):
        self.records[(record.tenant_id, record.approval_id)] = record

    async def get(self, tenant_id, approval_id):
        return self.records.get((tenant_id, approval_id))

    async def list_pending(self, tenant_id, *, limit):
        return [r for (t, _), r in self.records.items()
                if t == tenant_id and r.status == "pending"][:limit]

    async def decide(self, tenant_id, approval_id, *, approver_id, approved, now):
        key = (tenant_id, approval_id)
        record = self.records.get(key)
        if not record or record.status != "pending" or record.expires_at <= now:
            return False
        if record.requested_by == approver_id:
            return False
        from dataclasses import replace
        self.records[key] = replace(
            record, status="approved" if approved else "rejected",
            decided_by=approver_id,
        )
        return True


class ApprovalServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        self.repository = MemoryApprovalRepository()
        self.service = ApprovalService(self.repository, clock=lambda: self.now)

    async def test_high_risk_request_is_bound_to_exact_action_digest(self):
        record = await self.service.request(
            tenant_id="tenant-a", actor_id="user-a", action="email.send",
            resource="customer:42", parameters={"to": "a@example.test", "body": "hello"},
            risk_level=RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
        )
        self.assertEqual(record.status, "pending")
        self.assertEqual(len(record.action_hash), 64)
        self.assertEqual((await self.service.pending(tenant_id="tenant-a"))[0], record)
        self.assertEqual(await self.service.pending(tenant_id="tenant-b"), [])

    async def test_low_risk_and_invalid_ttl_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.service.request(
                tenant_id="t", actor_id="u", action="read", resource="x",
                parameters={}, risk_level=RiskLevel.READ_ONLY,
            )
        with self.assertRaises(ValueError):
            await self.service.request(
                tenant_id="t", actor_id="u", action="write", resource="x",
                parameters={}, risk_level=RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
                ttl=timedelta(days=2),
            )

    async def test_requester_cannot_approve_and_expiry_is_enforced(self):
        record = await self.service.request(
            tenant_id="tenant-a", actor_id="user-a", action="external.write",
            resource="x", parameters={}, risk_level=RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
        )
        with self.assertRaises(PermissionError):
            await self.service.decide(
                tenant_id="tenant-a", approval_id=record.approval_id,
                approver_id="user-a", approved=True,
            )
        self.assertTrue(await self.service.decide(
            tenant_id="tenant-a", approval_id=record.approval_id,
            approver_id="user-b", approved=True,
        ))
        self.assertFalse(await self.service.decide(
            tenant_id="tenant-a", approval_id=record.approval_id,
            approver_id="user-c", approved=True,
        ))

    async def test_expired_approval_cannot_be_decided(self):
        record = await self.service.request(
            tenant_id="tenant-a", actor_id="user-a", action="external.write",
            resource="x", parameters={}, risk_level=RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
            ttl=timedelta(seconds=1),
        )
        self.now += timedelta(seconds=2)
        self.assertFalse(await self.service.decide(
            tenant_id="tenant-a", approval_id=record.approval_id,
            approver_id="user-b", approved=True,
        ))


if __name__ == "__main__":
    unittest.main()
