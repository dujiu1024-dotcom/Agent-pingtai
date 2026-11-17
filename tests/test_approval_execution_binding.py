from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from agent_platform.approval.policy import ActionRequest
from agent_platform.approval.service import ApprovalRecord, ApprovalService


class ApprovalRepositoryFake:
    def __init__(self, record):
        self.record = record

    async def get(self, tenant_id, approval_id):
        if self.record.tenant_id != tenant_id or self.record.approval_id != approval_id:
            return None
        return self.record


class ApprovalExecutionBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_approved_action_can_be_reused(self):
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        request = ActionRequest(
            action="file.delete", resource="file-1",
            parameters={"path": "safe.txt"}, actor_id="user-1",
        )
        record = ApprovalRecord(
            approval_id="approval-1", tenant_id="tenant-1", run_id="run-1",
            action=request.action, resource=request.resource, risk_level=2,
            action_hash=request.digest(), requested_by=request.actor_id,
            decided_by="approver-1", status="approved",
            expires_at=now + timedelta(minutes=5),
        )
        service = ApprovalService(
            ApprovalRepositoryFake(record), clock=lambda: now
        )
        self.assertEqual(
            await service.approved_action_hash(
                tenant_id="tenant-1", approval_id="approval-1",
                request=request, run_id="run-1",
            ),
            request.digest(),
        )
        changed = ActionRequest(
            action="file.delete", resource="file-1",
            parameters={"path": "other.txt"}, actor_id="user-1",
        )
        self.assertIsNone(await service.approved_action_hash(
            tenant_id="tenant-1", approval_id="approval-1",
            request=changed, run_id="run-1",
        ))
        self.assertIsNone(await service.approved_action_hash(
            tenant_id="tenant-1", approval_id="approval-1",
            request=request, run_id="other-run",
        ))

    async def test_expired_or_revoked_approval_fails_closed(self):
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        request = ActionRequest(
            action="file.delete", resource="file-1",
            parameters={}, actor_id="user-1",
        )
        record = ApprovalRecord(
            approval_id="approval-1", tenant_id="tenant-1", run_id=None,
            action=request.action, resource=request.resource, risk_level=2,
            action_hash=request.digest(), requested_by=request.actor_id,
            decided_by="approver-1", status="approved",
            expires_at=now - timedelta(seconds=1),
        )
        service = ApprovalService(ApprovalRepositoryFake(record), clock=lambda: now)
        self.assertIsNone(await service.approved_action_hash(
            tenant_id="tenant-1", approval_id="approval-1", request=request,
        ))
        service.repository.record = replace(record, status="revoked", expires_at=now + timedelta(minutes=1))
        self.assertIsNone(await service.approved_action_hash(
            tenant_id="tenant-1", approval_id="approval-1", request=request,
        ))


    async def test_policy_revision_change_invalidates_an_approved_action(self):
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        request = ActionRequest(
            action="file.delete", resource="file-1",
            parameters={"path": "safe.txt"}, actor_id="user-1",
        )
        record = ApprovalRecord(
            approval_id="approval-policy", tenant_id="tenant-1", run_id="run-1",
            action=request.action, resource=request.resource, risk_level=2,
            action_hash=request.digest(), requested_by=request.actor_id,
            decided_by="approver-1", status="approved",
            expires_at=now + timedelta(minutes=5), policy_version="policy-v1",
        )
        service = ApprovalService(
            ApprovalRepositoryFake(record), clock=lambda: now,
            policy_version="policy-v2",
        )
        self.assertIsNone(await service.approved_action_hash(
            tenant_id="tenant-1", approval_id="approval-policy",
            request=request, run_id="run-1",
        ))
