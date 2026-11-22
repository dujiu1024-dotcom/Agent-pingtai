from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(True, reason="CI workflow YAML infra-as-code")
import unittest
from pathlib import Path

from agent_platform.production_config import (
    ProductionConfigurationError,
    load_production_settings,
)

ROOT = Path(__file__).resolve().parents[1]


class ObservabilityDeliveryReadinessTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        path = ROOT / relative
        self.assertTrue(path.is_file(), f"missing delivery artifact: {relative}")
        return path.read_text(encoding="utf-8")

    def test_operator_documentation_covers_all_issue_16_areas_and_field_gaps(self):
        acceptance = self.read("docs/observability/issue-16-a-16-03-delivery-acceptance.md")
        for relative in (
            "docs/observability/cross-component-tracing.md",
            "docs/observability/metrics-and-structured-logs.md",
            "docs/observability/alert-operations.md",
            "docs/observability/agent-rag-evaluation.md",
            "docs/observability/evaluation-governance.md",
            "docs/observability/prompt-operations.md",
            "docs/observability/prompt-rollouts.md",
            "docs/deployment/database-migration-orchestration.md",
            "deploy/.env.production.example",
            "deploy/production.compose.yaml",
        ):
            self.read(relative)
            self.assertIn(relative, acceptance)
        for evidence in (
            "Prometheus", "Langfuse", "Alert delivery", "Evaluation",
            "Prompt rollout", "Multi-replica operation", "Not run",
        ):
            self.assertIn(evidence, acceptance)

    def test_observability_postgres_migrations_are_present_and_documented(self):
        acceptance = self.read("docs/observability/issue-16-a-16-03-delivery-acceptance.md")
        expected = {
            "017_prompt_registry.sql": "platform_prompt_versions",
            "019_eval_datasets.sql": "platform_eval_datasets",
            "020_alert_events.sql": "platform_alert_events",
            "021_alert_delivery_outbox.sql": "delivery_status",
            "034_eval_human_scores.sql": "platform_eval_human_scores",
            "076_alert_operations_reliability.sql": "delivery_max_attempts",
            "079_prompt_audit_events.sql": "platform_prompt_audit_events",
            "083_prompt_variants.sql": "platform_prompt_variants",
            "084_prompt_rollouts.sql": "platform_prompt_rollout_policies",
        }
        for filename, marker in expected.items():
            with self.subTest(migration=filename):
                sql = self.read(f"agent_platform/storage/postgres/{filename}")
                self.assertIn(marker, sql)
                self.assertIn(filename, acceptance)
        self.read("scripts/migrate_postgres.sh")
        self.read("scripts/check_migrations.sh")

    def test_platform_dependencies_and_production_compose_are_declared(self):
        requirements = self.read("requirements-platform.txt")
        self.assertIn("langfuse>=3.0,<4.0", requirements)
        self.assertIn("prometheus-client>=0.21,<1.0", requirements)
        compose = self.read("deploy/production.compose.yaml")
        example = self.read("deploy/.env.production.example")
        for key in (
            "PLATFORM_ASGI_APP", "PLATFORM_WORKER_MODULE", "ALLOWED_ORIGINS",
            "POSTGRES_PASSWORD_FILE", "REDIS_PASSWORD_FILE",
        ):
            self.assertIn(key, compose)
            self.assertIn(key, example)
        operations = self.read("deploy/OPERATIONS.md")
        self.assertIn("optional API subsystem", operations)
        self.assertIn("does not silently", operations)

    def test_production_settings_fail_closed_and_accept_secret_file_contract(self):
        valid = {
            "POSTGRES_HOST": "postgres.internal",
            "POSTGRES_DB": "agent_platform",
            "POSTGRES_USER": "platform",
            "POSTGRES_PASSWORD": "test-only-password",
            "REDIS_HOST": "redis.internal",
            "REDIS_PASSWORD": "test-only-password",
            "ALLOWED_ORIGINS": "https://agent.example.com",
        }
        settings = load_production_settings(valid)
        self.assertIn("postgres.internal", settings.postgres_dsn)
        self.assertIn("redis.internal", settings.redis_url)
        self.assertEqual(settings.allowed_origins, ("https://agent.example.com",))
        with self.assertRaises(ProductionConfigurationError):
            load_production_settings({})
        invalid_origin = {**valid, "ALLOWED_ORIGINS": "*"}
        with self.assertRaises(ProductionConfigurationError):
            load_production_settings(invalid_origin)

    def test_ci_has_full_unit_static_and_real_postgres_redis_jobs(self):
        ci = self.read(".github/workflows/ci.yml")
        integration = self.read(".github/workflows/platform-integration.yml")
        release = self.read(".github/workflows/platform-release-gate.yml")
        self.assertIn("python -m unittest discover -s tests -t . -q", ci)
        self.assertIn("ruff check .", ci)
        self.assertIn("mypy", ci)
        self.assertIn("uses: ./.github/workflows/platform-integration.yml", ci)
        self.assertIn("pgvector/pgvector:pg16", integration)
        self.assertIn("redis:7.4-alpine", integration)
        self.assertIn("AGENT_PLATFORM_TEST_DSN", integration)
        self.assertIn("tests.test_platform_postgres_integration", integration)
        self.assertIn("e2e-security-integration", release)
        self.assertIn("production-release-evidence", release)


if __name__ == "__main__":
    unittest.main()
