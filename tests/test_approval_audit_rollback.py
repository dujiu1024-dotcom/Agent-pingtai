from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

from agent_platform.approval.service import ApprovalRecord, PostgresApprovalRepository


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.snapshot = copy.deepcopy(
            (self.connection.approvals, self.connection.audit_rows)
        )
        self.connection.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.connection.in_transaction = False
        if exc_type is not None:
            (
                self.connection.approvals,
                self.connection.audit_rows,
            ) = self.snapshot
        return False


class _FailingAuditConnection:
    """Transactional fake that fails only the append-only audit INSERT."""

    def __init__(self, *, approval=None):
        self.approvals = [] if approval is None else [copy.deepcopy(approval)]
        self.audit_rows = []
        self.in_transaction = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return _Transaction(self)

    async def execute(self, query, params):
        if not self.in_transaction:
            raise AssertionError("approval and audit writes must share a transaction")
        if "INSERT INTO platform_approvals" in query:
            self.approvals.append({"approval_id": params[1], "status": "pending"})
            return _Cursor()
        if "UPDATE platform_approvals" in query:
            row = self.approvals[0]
            if "SET status='revoked'" in query:
                row["status"] = "revoked"
            elif "SET status=%s" in query:
                row["status"] = params[0]
            return _Cursor({
                "action": "external.write",
                "resource": "account:7",
                "action_hash": "a" * 64,
                "run_id": None,
            })
        if "INSERT INTO platform_tool_audit" in query:
            raise RuntimeError("audit database unavailable")
        raise AssertionError(f"unexpected SQL: {query}")


class PostgresApprovalAuditRollbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        self.approval_id = "00000000-0000-0000-0000-000000000015"
        self.pending = {
            "approval_id": UUID(self.approval_id),
            "status": "pending",
        }

    async def repository(self, connection):
        async def connect():
            return connection

        return PostgresApprovalRepository(connect)

    async def test_create_rolls_back_approval_when_audit_insert_fails(self):
        connection = _FailingAuditConnection()
        repository = await self.repository(connection)
        record = ApprovalRecord(
            approval_id=self.approval_id,
            tenant_id="tenant-a",
            run_id=None,
            action="external.write",
            resource="account:7",
            risk_level=3,
            action_hash="a" * 64,
            requested_by="requester",
            decided_by=None,
            status="pending",
            expires_at=self.now + timedelta(minutes=5),
        )

        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await repository.create(record)

        self.assertEqual(connection.approvals, [])
        self.assertEqual(connection.audit_rows, [])

    async def test_revoke_rolls_back_status_when_audit_insert_fails(self):
        connection = _FailingAuditConnection(approval=self.pending)
        repository = await self.repository(connection)

        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await repository.revoke(
                "tenant-a",
                self.approval_id,
                actor_id="requester",
                now=self.now,
            )

        self.assertEqual(connection.approvals[0]["status"], "pending")
        self.assertEqual(connection.audit_rows, [])

    async def test_decide_rolls_back_status_when_audit_insert_fails(self):
        connection = _FailingAuditConnection(approval=self.pending)
        repository = await self.repository(connection)

        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await repository.decide(
                "tenant-a",
                self.approval_id,
                approver_id="approver",
                approved=True,
                now=self.now,
            )

        self.assertEqual(connection.approvals[0]["status"], "pending")
        self.assertEqual(connection.audit_rows, [])


if __name__ == "__main__":
    unittest.main()
