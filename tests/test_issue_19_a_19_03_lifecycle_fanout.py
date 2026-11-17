from __future__ import annotations

import unittest

from agent_platform.data_governance.export import SubjectExportCoordinator
from agent_platform.data_governance.lifecycle import DeletionCoordinator


SYSTEMS = (
    "primary_database",
    "object_storage",
    "vector_index",
    "cache",
    "connector_replica",
)


class DeletionAdapter:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def delete_subject(self, *, tenant_id, subject_id, operation_id):
        self.calls.append((tenant_id, subject_id, operation_id))
        if self.fail:
            raise RuntimeError("backend unavailable")


class DeletionLedger:
    def __init__(self):
        self.subjects = {}
        self.systems = {}
        self.status = {}

    async def begin_operation(self, *, tenant_id, operation_id, subject_id):
        key = (tenant_id, operation_id)
        existing = self.subjects.setdefault(key, subject_id)
        if existing != subject_id:
            raise ValueError("operation id is already bound to another subject")
        self.status[key] = "running"

    async def completed_systems(self, *, tenant_id, operation_id):
        return tuple(
            system
            for (tenant, operation, system), state in self.systems.items()
            if tenant == tenant_id and operation == operation_id
            and state["status"] == "completed"
        )

    async def mark_system_started(self, *, tenant_id, operation_id, system):
        self.systems[(tenant_id, operation_id, system)] = {"status": "running"}

    async def mark_system_completed(self, *, tenant_id, operation_id, system):
        self.systems[(tenant_id, operation_id, system)] = {"status": "completed"}

    async def mark_system_failed(self, *, tenant_id, operation_id, system, error_code):
        self.systems[(tenant_id, operation_id, system)] = {
            "status": "failed", "error_code": error_code,
        }

    async def finish_operation(self, *, tenant_id, operation_id, complete):
        self.status[(tenant_id, operation_id)] = "completed" if complete else "partial"


class ExportAdapter:
    def __init__(self, source):
        self.source = source
        self.calls = []

    async def export_subject(self, *, tenant_id, subject_id):
        self.calls.append((tenant_id, subject_id))
        yield {
            "tenant_id": tenant_id,
            "subject_id": subject_id,
            "source": self.source,
            "record_id": f"{self.source}:{subject_id}",
        }


class ExportLedger:
    def __init__(self):
        self.subjects = {}
        self.sources = {}
        self.status = {}

    async def begin_operation(self, *, tenant_id, operation_id, subject_id):
        key = (tenant_id, operation_id)
        existing = self.subjects.setdefault(key, subject_id)
        if existing != subject_id:
            raise ValueError("operation id is already bound to another subject")
        self.status[key] = "running"

    async def completed_sources(self, *, tenant_id, operation_id):
        return tuple(
            source
            for (tenant, operation, source), state in self.sources.items()
            if tenant == tenant_id and operation == operation_id
            and state["status"] == "completed"
        )

    async def mark_source_started(self, *, tenant_id, operation_id, source):
        self.sources[(tenant_id, operation_id, source)] = {"status": "running"}

    async def mark_source_completed(
        self, *, tenant_id, operation_id, source, record_count, sha256,
    ):
        self.sources[(tenant_id, operation_id, source)] = {
            "status": "completed", "record_count": record_count, "sha256": sha256,
        }

    async def mark_source_failed(self, *, tenant_id, operation_id, source, error_code):
        self.sources[(tenant_id, operation_id, source)] = {
            "status": "failed", "error_code": error_code,
        }

    async def finish_operation(self, *, tenant_id, operation_id, complete):
        self.status[(tenant_id, operation_id)] = "completed" if complete else "partial"


class ExportSink:
    def __init__(self):
        self.records = {}

    async def write_record(self, *, operation_id, source, ordinal, record):
        self.records[(operation_id, source, ordinal)] = record


class Issue19A1903LifecycleFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_fans_out_to_all_five_required_data_domains(self):
        adapters = {name: DeletionAdapter() for name in SYSTEMS}
        result = await DeletionCoordinator(adapters).delete(
            tenant_id="tenant-a",
            subject_id="subject-1",
            operation_id="delete-all-domains",
        )

        self.assertTrue(result.complete)
        self.assertEqual(set(result.completed), set(SYSTEMS))
        self.assertEqual(result.failed, {})
        for name, adapter in adapters.items():
            with self.subTest(system=name):
                self.assertEqual(
                    adapter.calls,
                    [("tenant-a", "subject-1", "delete-all-domains")],
                )

    async def test_delete_retry_resumes_failed_domains_without_repeating_completed_domains(self):
        adapters = {name: DeletionAdapter() for name in SYSTEMS}
        adapters["vector_index"].fail = True
        ledger = DeletionLedger()
        coordinator = DeletionCoordinator(adapters)

        first = await coordinator.delete(
            tenant_id="tenant-a",
            subject_id="subject-1",
            operation_id="delete-retry",
            ledger=ledger,
        )
        self.assertFalse(first.complete)
        self.assertEqual(first.failed, {"vector_index": "RuntimeError"})
        self.assertEqual(ledger.status[("tenant-a", "delete-retry")], "partial")

        adapters["vector_index"].fail = False
        second = await coordinator.delete(
            tenant_id="tenant-a",
            subject_id="subject-1",
            operation_id="delete-retry",
            ledger=ledger,
        )
        self.assertTrue(second.complete)
        self.assertEqual(set(second.completed), set(SYSTEMS))
        self.assertEqual(ledger.status[("tenant-a", "delete-retry")], "completed")
        for name, adapter in adapters.items():
            with self.subTest(system=name):
                expected_calls = 2 if name == "vector_index" else 1
                self.assertEqual(len(adapter.calls), expected_calls)

    async def test_export_streams_all_five_domains_with_tenant_subject_scope_and_checksums(self):
        adapters = {name: ExportAdapter(name) for name in SYSTEMS}
        sink = ExportSink()
        ledger = ExportLedger()
        result = await SubjectExportCoordinator(adapters).export(
            tenant_id="tenant-a",
            subject_id="subject-1",
            operation_id="export-all-domains",
            sink=sink,
            ledger=ledger,
        )

        self.assertTrue(result.complete)
        self.assertEqual(set(result.completed), set(SYSTEMS))
        self.assertEqual({item.source for item in result.sources}, set(SYSTEMS))
        self.assertEqual(ledger.status[("tenant-a", "export-all-domains")], "completed")
        self.assertEqual(len(sink.records), len(SYSTEMS))
        for name, adapter in adapters.items():
            with self.subTest(source=name):
                self.assertEqual(adapter.calls, [("tenant-a", "subject-1")])
                payload = sink.records[("export-all-domains", name, 0)]
                self.assertIn(b'"tenant_id":"tenant-a"', payload)
                self.assertIn(b'"subject_id":"subject-1"', payload)
                self.assertIn(f'"source":"{name}"'.encode(), payload)
        self.assertTrue(all(item.record_count == 1 for item in result.sources))
        self.assertTrue(all(len(item.sha256) == 64 for item in result.sources))


if __name__ == "__main__":
    unittest.main()
