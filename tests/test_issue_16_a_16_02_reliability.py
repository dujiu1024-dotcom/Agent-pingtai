"""Dependency-failure contracts for Issue 16 observability persistence."""

from __future__ import annotations

import unittest

from agent_platform.observability.alert_store import PostgresAlertEventStore
from agent_platform.observability.evaluation_store import PostgresEvaluationRepository
from agent_platform.observability.prompt_store import PostgresPromptRegistry


class ObservabilityDependencyFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_alert_store_does_not_fallback_when_postgres_is_unavailable(self):
        async def unavailable():
            raise ConnectionError("postgres unavailable")

        store = PostgresAlertEventStore(unavailable)
        with self.assertRaisesRegex(ConnectionError, "postgres unavailable"):
            await store.list_open(scope_key="tenant-unavailable")

    async def test_evaluation_repository_does_not_return_empty_history_on_db_failure(self):
        async def unavailable():
            raise ConnectionError("postgres unavailable")

        repository = PostgresEvaluationRepository(unavailable)
        with self.assertRaisesRegex(ConnectionError, "postgres unavailable"):
            await repository.latest_reports("dataset-a", tenant_id="tenant-a")

    async def test_prompt_registry_does_not_hide_storage_failure_as_missing_version(self):
        async def unavailable():
            raise ConnectionError("postgres unavailable")

        registry = PostgresPromptRegistry(unavailable)
        with self.assertRaisesRegex(ConnectionError, "postgres unavailable"):
            await registry.resolve("prompt-a", version=1)


if __name__ == "__main__":
    unittest.main()
