from __future__ import annotations

import unittest

from agent_platform.data_governance.export import SubjectExportCoordinator


class FakeAdapter:
    def __init__(self, records, fail_after=None):
        self.records = records
        self.fail_after = fail_after
        self.calls = []

    async def export_subject(self, *, tenant_id, subject_id):
        self.calls.append((tenant_id, subject_id))
        for index, record in enumerate(self.records):
            if self.fail_after is not None and index >= self.fail_after:
                raise RuntimeError("sensitive backend detail")
            yield record


class FakeSink:
    def __init__(self):
        self.records = {}

    async def write_record(self, *, operation_id, source, ordinal, record):
        self.records[(operation_id, source, ordinal)] = record


class SubjectExportCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_records_and_returns_stable_source_checksums(self):
        adapter = FakeAdapter([{"id": "1", "value": "a"}, {"value": "b", "id": "2"}])
        sink = FakeSink()
        result = await SubjectExportCoordinator({"postgres": adapter}).export(
            tenant_id="tenant-a", subject_id="user-1",
            operation_id="export-1", sink=sink,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.completed, ("postgres",))
        self.assertEqual(result.sources[0].record_count, 2)
        self.assertEqual(
            sink.records[("export-1", "postgres", 0)],
            b'{"id":"1","value":"a"}',
        )
        self.assertEqual(
            sink.records[("export-1", "postgres", 1)],
            b'{"id":"2","value":"b"}',
        )

    async def test_partial_failure_is_reported_and_completed_sources_can_resume(self):
        good = FakeAdapter([{"id": "1"}])
        broken = FakeAdapter([{"id": "2"}, {"id": "3"}], fail_after=1)
        coordinator = SubjectExportCoordinator({"postgres": good, "objects": broken})
        sink = FakeSink()
        first = await coordinator.export(
            tenant_id="tenant-a", subject_id="user-1",
            operation_id="export-2", sink=sink,
        )
        self.assertFalse(first.complete)
        self.assertEqual(first.completed, ("postgres",))
        self.assertEqual(first.failed, {"objects": "RuntimeError"})
        broken.fail_after = None
        second = await coordinator.export(
            tenant_id="tenant-a", subject_id="user-1",
            operation_id="export-2", sink=sink,
            completed_sources=first.completed,
        )
        self.assertTrue(second.complete)
        self.assertEqual(second.completed, ("postgres", "objects"))
        self.assertEqual(len(good.calls), 1)

    async def test_rejects_unknown_completed_sources_and_non_mapping_records(self):
        with self.assertRaises(ValueError):
            await SubjectExportCoordinator({"postgres": FakeAdapter([])}).export(
                tenant_id="tenant-a", subject_id="user-1",
                operation_id="export-3", sink=FakeSink(),
                completed_sources=("unknown",),
            )
        result = await SubjectExportCoordinator({"postgres": FakeAdapter(["bad"])}).export(
            tenant_id="tenant-a", subject_id="user-1",
            operation_id="export-4", sink=FakeSink(),
        )
        self.assertEqual(result.failed, {"postgres": "TypeError"})


if __name__ == "__main__":
    unittest.main()
