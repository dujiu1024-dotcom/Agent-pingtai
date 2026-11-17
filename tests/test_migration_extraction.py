from __future__ import annotations

import unittest

from agent_platform.data_governance.extraction import (
    ExtractionPage, MigrationExtractor,
)


class FakeSource:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def extract_page(self, *, cursor, limit):
        self.calls.append((cursor, limit))
        return self.pages[cursor]


class FakeStaging:
    def __init__(self, cursor=None):
        self.cursor = cursor
        self.rows = []
        self.checkpoints = []

    async def get_checkpoint(self, *, tenant_id, batch_id):
        return self.cursor

    async def append_page(self, *, tenant_id, batch_id, rows, next_cursor):
        self.rows.extend(dict(row) for row in rows)
        self.cursor = next_cursor
        self.checkpoints.append(next_cursor)
        return len(rows)

    async def iter_rows(self, *, tenant_id, batch_id):
        return tuple(self.rows)


class MigrationExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_pages_and_persists_cursor_after_each_page(self):
        staging = FakeStaging()
        source = FakeSource({
            None: ExtractionPage(({"id": "1"},), "page-2"),
            "page-2": ExtractionPage(({"id": "2"},), None),
        })
        report = await MigrationExtractor(staging=staging, page_size=10).extract(
            tenant_id="tenant-a", batch_id="batch-a", source=source,
        )
        self.assertTrue(report.completed)
        self.assertEqual(report.extracted_count, 2)
        self.assertEqual(report.pages_read, 2)
        self.assertEqual(staging.checkpoints, ["page-2", None])
        self.assertEqual([row["id"] for row in staging.rows], ["1", "2"])

    async def test_resumes_from_durable_cursor(self):
        staging = FakeStaging(cursor="page-2")
        source = FakeSource({"page-2": ExtractionPage(({"id": "2"},), None)})
        report = await MigrationExtractor(staging=staging).extract(
            tenant_id="tenant-a", batch_id="batch-a", source=source,
        )
        self.assertEqual(report.resumed_from_cursor, "page-2")
        self.assertEqual(source.calls, [("page-2", 500)])
        self.assertEqual(report.extracted_count, 1)

    async def test_rejects_non_advancing_cursor(self):
        staging = FakeStaging()
        source = FakeSource({
            None: ExtractionPage(({"id": "1"},), "same"),
            "same": ExtractionPage(({"id": "2"},), "same"),
        })
        with self.assertRaisesRegex(ValueError, "did not advance"):
            await MigrationExtractor(staging=staging).extract(
                tenant_id="tenant-a", batch_id="batch-a", source=source,
            )

    async def test_rejects_empty_page_with_cursor(self):
        staging = FakeStaging()
        source = FakeSource({None: ExtractionPage((), "page-2")})
        with self.assertRaisesRegex(ValueError, "empty extraction page"):
            await MigrationExtractor(staging=staging).extract(
                tenant_id="tenant-a", batch_id="batch-a", source=source,
            )

    async def test_rejects_invalid_page_size(self):
        with self.assertRaises(ValueError):
            MigrationExtractor(staging=FakeStaging(), page_size=0)


if __name__ == "__main__":
    unittest.main()
