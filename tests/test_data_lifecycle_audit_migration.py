from __future__ import annotations

import unittest
from pathlib import Path


class LifecycleAuditMigrationTests(unittest.TestCase):
    def test_audit_ledger_is_tenant_scoped_and_append_only(self):
        path = Path(__file__).parents[1] / "agent_platform/storage/postgres/071_data_lifecycle_audit.sql"
        sql = path.read_text(encoding="utf-8").lower()
        self.assertIn("create table if not exists platform_data_lifecycle_audit", sql)
        self.assertIn("tenant_id text not null", sql)
        self.assertIn("correlation_id uuid not null", sql)
        self.assertIn("attempt integer not null check (attempt > 0)", sql)
        self.assertIn("before update or delete", sql)
        self.assertIn("before truncate", sql)
        self.assertIn("evidence_sha256", sql)


if __name__ == "__main__":
    unittest.main()
