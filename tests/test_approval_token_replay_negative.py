from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

from agent_platform.approval.postgres import PostgresApprovalVerifier


class FakeCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeApprovalConnection:
    """Small SQL-contract fake for the atomic approval-consumption UPDATE."""

    def __init__(self, *, tenant_id, approval_id, action_digest, policy_version,
                 status, expires_at, now):
        self.row = {
            "tenant_id": tenant_id,
            "approval_id": UUID(approval_id),
            "action_digest": action_digest,
            "policy_version": policy_version,
            "status": status,
            "expires_at": expires_at,
        }
        self.now = now
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, params):
        self.calls.append((query, params))
        tenant_id, approval_id, action_digest, policy_version = params
        accepted = (
            self.row["tenant_id"] == tenant_id
            and self.row["approval_id"] == approval_id
            and self.row["action_digest"] == action_digest
            and self.row["policy_version"] == policy_version
            and self.row["status"] == "approved"
            and self.row["expires_at"] > self.now
        )
        if not accepted:
            return FakeCursor(None)
        self.row["status"] = "consumed"
        return FakeCursor({"approval_id": approval_id})


class PostgresApprovalVerifierReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        self.approval_id = "00000000-0000-0000-0000-000000000015"
        self.digest = "a" * 64
        self.policy_version = "policy-v3"

    def make_verifier(self, *, tenant_id="tenant-a", status="approved",
                      expires_at=None):
        connection = FakeApprovalConnection(
            tenant_id=tenant_id,
            approval_id=self.approval_id,
            action_digest=self.digest,
            policy_version=self.policy_version,
            status=status,
            expires_at=expires_at or self.now + timedelta(minutes=5),
            now=self.now,
        )

        async def connect():
            return connection

        return PostgresApprovalVerifier(connect), connection

    async def test_approved_token_can_be_consumed_only_once(self):
        verifier, connection = self.make_verifier()

        self.assertTrue(await verifier.consume(
            tenant_id="tenant-a",
            approval_id=self.approval_id,
            action_digest=self.digest,
            policy_version=self.policy_version,
        ))
        self.assertFalse(await verifier.consume(
            tenant_id="tenant-a",
            approval_id=self.approval_id,
            action_digest=self.digest,
            policy_version=self.policy_version,
        ))
        self.assertEqual(connection.row["status"], "consumed")
        self.assertEqual(len(connection.calls), 2)

    async def test_expired_approval_token_is_rejected(self):
        verifier, connection = self.make_verifier(
            expires_at=self.now - timedelta(microseconds=1),
        )

        self.assertFalse(await verifier.consume(
            tenant_id="tenant-a",
            approval_id=self.approval_id,
            action_digest=self.digest,
            policy_version=self.policy_version,
        ))
        self.assertEqual(connection.row["status"], "approved")

    async def test_already_consumed_token_replay_is_rejected(self):
        verifier, connection = self.make_verifier(status="consumed")

        self.assertFalse(await verifier.consume(
            tenant_id="tenant-a",
            approval_id=self.approval_id,
            action_digest=self.digest,
            policy_version=self.policy_version,
        ))
        self.assertEqual(connection.row["status"], "consumed")

    async def test_cross_tenant_token_replay_is_rejected(self):
        verifier, connection = self.make_verifier(tenant_id="tenant-a")

        self.assertFalse(await verifier.consume(
            tenant_id="tenant-b",
            approval_id=self.approval_id,
            action_digest=self.digest,
            policy_version=self.policy_version,
        ))
        self.assertEqual(connection.row["status"], "approved")

    async def test_consume_query_is_tenant_and_state_scoped(self):
        verifier, connection = self.make_verifier()
        await verifier.consume(
            tenant_id="tenant-a",
            approval_id=self.approval_id,
            action_digest=self.digest,
            policy_version=self.policy_version,
        )
        query, params = connection.calls[0]
        self.assertIn("WHERE tenant_id = %s", query)
        self.assertIn("AND status = 'approved'", query)
        self.assertIn("AND expires_at > now()", query)
        self.assertEqual(params, ("tenant-a", UUID(self.approval_id), self.digest, self.policy_version))


if __name__ == "__main__":
    unittest.main()
