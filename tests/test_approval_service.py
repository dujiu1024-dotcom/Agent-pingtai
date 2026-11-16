from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.approvals import create_approval_router
from agent_platform.approval.policy import RiskLevel
from agent_platform.approval.service import ApprovalService
from agent_platform.identity.models import Principal


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

    async def revoke(self, tenant_id, approval_id, *, actor_id, now, allow_admin=False):
        key = (tenant_id, approval_id)
        record = self.records.get(key)
        if not record or record.status != "pending" or record.expires_at <= now:
            return False
        if record.requested_by != actor_id and not allow_admin:
            return False
        from dataclasses import replace
        self.records[key] = replace(record, status="revoked", decided_by=actor_id)
        return True

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


    def test_api_uses_authenticated_tenant_and_approver_authorization(self):
        repository = MemoryApprovalRepository()
        service = ApprovalService(repository, clock=lambda: self.now)
        app = FastAPI()
        app.include_router(create_approval_router(
            service=service, authorize_approver=lambda actor: actor.user_id == "approver",
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.scope.setdefault("state", {})["identity.principal"] = Principal(
                user_id="requester", tenant_id="tenant-a", session_id="s",
                email="r@example.test", display_name="Requester",
            )
            return await call_next(request)

        client = TestClient(app)
        created = client.post("/api/platform/v1/approvals", json={
            "action": "external.write", "resource": "account:7",
            "risk_level": 2, "parameters": {"amount": 10},
            "tenant_id": "tenant-attacker", "requested_by": "attacker",
        })
        self.assertEqual(created.status_code, 201)
        approval_id = created.json()["approval_id"]
        detail = client.get(f"/api/platform/v1/approvals/{approval_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["requested_by"], "requester")
        self.assertEqual(detail.json()["status"], "pending")
        self.assertIn(("tenant-a", approval_id), repository.records)
        self.assertNotIn(("tenant-attacker", approval_id), repository.records)
        self.assertEqual(client.get("/api/platform/v1/approvals").status_code, 403)

    async def test_requester_can_revoke_and_other_user_cannot(self):
        record = await self.service.request(
            tenant_id="tenant-a", actor_id="user-a", action="external.write",
            resource="x", parameters={"value": 1},
            risk_level=RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
        )
        with self.assertRaises(PermissionError):
            await self.service.revoke(
                tenant_id="tenant-a", approval_id=record.approval_id,
                actor_id="user-b",
            )
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a", approval_id=record.approval_id,
            actor_id="user-a",
        ))
        self.assertEqual(
            (await self.repository.get("tenant-a", record.approval_id)).status,
            "revoked",
        )

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


    def test_action_digest_rejects_non_finite_numbers(self):
        from agent_platform.approval.policy import ActionRequest

        for value in (float("nan"), float("inf"), float("-inf"), {"nested": [float("nan")]}):
            request = ActionRequest(
                action="external.write", resource="account:7",
                parameters={"value": value}, actor_id="user-a",
            )
            with self.assertRaises(ValueError):
                request.digest()

    def test_action_digest_is_stable_and_binds_actor_target_and_parameters(self):
        from agent_platform.approval.policy import ActionRequest

        base = ActionRequest(
            action="external.write", resource="account:7",
            parameters={"amount": 10, "memo": "invoice"}, actor_id="user-a",
        )
        self.assertEqual(base.digest(), base.digest())
        self.assertNotEqual(base.digest(), ActionRequest(
            action="external.write", resource="account:8",
            parameters={"amount": 10, "memo": "invoice"}, actor_id="user-a",
        ).digest())
        self.assertNotEqual(base.digest(), ActionRequest(
            action="external.write", resource="account:7",
            parameters={"amount": 11, "memo": "invoice"}, actor_id="user-a",
        ).digest())
        self.assertNotEqual(base.digest(), ActionRequest(
            action="external.write", resource="account:7",
            parameters={"amount": 10, "memo": "invoice"}, actor_id="user-b",
        ).digest())


if __name__ == "__main__":
    unittest.main()
