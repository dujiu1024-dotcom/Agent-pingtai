from __future__ import annotations

import unittest

from agent_platform.events.admin import WebhookSubscriptionService


class _Cursor:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, *, claimed=True):
        self.claimed = claimed
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def transaction(self):
        return self

    async def execute(self, query, params):
        self.statements.append((query, params))
        if "WITH due AS" in query:
            return _Cursor(rows=[{
                "tenant_id": "tenant-a",
                "subscription_id": "00000000-0000-0000-0000-000000000001",
                "secret_ref": "secret://tenant-a/old",
                "lease_token": "00000000-0000-0000-0000-000000000002",
            }])
        return _Cursor(row=("secret://tenant-a/old",) if self.claimed else None)


class _Store:
    def __init__(self, connection):
        self.connection = connection

    async def _connect(self):
        return self.connection


class _Secrets:
    def __init__(self):
        self.revoked = []

    async def revoke(self, *, secret_ref):
        self.revoked.append(secret_ref)


class WebhookSecretRevocationLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_uses_skip_locked_and_completion_is_fenced(self):
        connection = _Connection()
        secrets = _Secrets()
        service = WebhookSubscriptionService(
            store=_Store(connection), secrets=secrets, revocation_lease_seconds=45,
        )

        result = await service.retry_pending_secret_revocations(limit=10)

        self.assertEqual(result, {"claimed": 1, "revoked": 1, "failed": 0})
        self.assertEqual(secrets.revoked, ["secret://tenant-a/old"])
        claim_sql, claim_params = connection.statements[0]
        complete_sql, complete_params = connection.statements[1]
        self.assertIn("FOR UPDATE SKIP LOCKED", claim_sql)
        self.assertIn("lease_token=gen_random_uuid()", claim_sql)
        self.assertEqual(claim_params, (10, 45))
        self.assertIn("lease_token=%s", complete_sql)
        self.assertIn("lease_until > now()", complete_sql)
        self.assertEqual(complete_params[-1], "00000000-0000-0000-0000-000000000002")

    async def test_expired_or_reclaimed_lease_cannot_acknowledge(self):
        connection = _Connection(claimed=False)
        secrets = _Secrets()
        service = WebhookSubscriptionService(store=_Store(connection), secrets=secrets)

        result = await service.retry_pending_secret_revocations()

        self.assertEqual(result, {"claimed": 1, "revoked": 0, "failed": 1})
        self.assertEqual(secrets.revoked, ["secret://tenant-a/old"])


if __name__ == "__main__":
    unittest.main()
