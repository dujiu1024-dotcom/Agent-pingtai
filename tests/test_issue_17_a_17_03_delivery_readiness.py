"""Repository readiness contract for Issue #17 / A-17-03.

Static inventory only: it is not a production integration test and explicitly
records the current production composition gap.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Issue17DeliveryReadinessTests(unittest.TestCase):
    def test_quota_operations_and_deployment_artifacts_are_present(self):
        required = (
            "docs/quotas/administration-runtime-integration.md",
            "docs/quotas/hierarchical-policy-model.md",
            "docs/quotas/concurrency-admission.md",
            "docs/quotas/usage-and-cost-reporting.md",
            "docs/deployment/database-migration-orchestration.md",
            "agent_platform/quotas/postgres.py",
            "agent_platform/quotas/concurrency.py",
            "agent_platform/quotas/pricing.py",
            "agent_platform/quotas/pricing_store.py",
            "agent_platform/quotas/storage_meter.py",
            "agent_platform/storage/postgres/012_tenant_quotas_cost.sql",
            "agent_platform/storage/postgres/037_queue_worker_concurrency_quotas.sql",
            "agent_platform/storage/postgres/039_quota_reservation_leases.sql",
            "agent_platform/storage/postgres/042_root_run_quota_attribution.sql",
            "agent_platform/storage/postgres/044_model_pricing_catalog.sql",
            "agent_platform/storage/postgres/071_hierarchical_quota_policies.sql",
            "requirements-platform.txt",
            "deploy/.env.example",
            "deploy/.env.production.example",
            "deploy/compose.yaml",
            "deploy/production.compose.yaml",
            "scripts/migrate_postgres.sh",
            "scripts/check_migrations.sh",
        )
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"missing quota delivery artifacts: {missing}")

    def test_docs_disclose_process_local_fairness_and_external_price_scope(self):
        concurrency = (ROOT / "docs/quotas/concurrency-admission.md").read_text()
        reporting = (ROOT / "docs/quotas/usage-and-cost-reporting.md").read_text()
        self.assertIn("process-local", concurrency)
        self.assertIn("multiple application processes", concurrency)
        self.assertIn("not fabricated", reporting)
        self.assertIn("Invoice-level reconciliation", reporting)

    def test_production_composition_gap_is_explicit_not_claimed_as_wired(self):
        composition = (ROOT / "agent_platform/production.py").read_text()
        acceptance = (ROOT / "docs/quotas/issue-17-a-17-03-delivery-acceptance.md").read_text()
        self.assertIn("PostgresRunStateStore", composition)
        self.assertNotIn("PostgresQuotaStore", composition)
        self.assertIn("Quota runtime is not part of production ASGI composition", acceptance)
        self.assertIn("Real S3-compatible tenant-prefix measurement", acceptance)

    def test_production_template_uses_secret_files_and_immutable_image(self):
        template = (ROOT / "deploy/.env.production.example").read_text()
        compose = (ROOT / "deploy/production.compose.yaml").read_text()
        self.assertIn("PLATFORM_API_IMAGE=ghcr.io/your-org/agent-platform@sha256:", template)
        self.assertIn("POSTGRES_PASSWORD_FILE", template)
        self.assertIn("REDIS_PASSWORD_FILE", template)
        self.assertIn("MINIO_ROOT_PASSWORD_FILE", template)
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)


if __name__ == "__main__":
    unittest.main()
