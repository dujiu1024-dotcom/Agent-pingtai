from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.migration_diff import load_jsonl, validate_input_paths


class MigrationDiffCliTests(unittest.TestCase):
    def test_rejects_symlinked_source_and_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            target = root / "target.jsonl"
            source.write_text('{"id":"1"}\n', encoding="utf-8")
            target.write_text('{"id":"1"}\n', encoding="utf-8")
            source_link = root / "source-link.jsonl"
            try:
                source_link.symlink_to(source)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(ValueError, "source input must not be a symlink"):
                validate_input_paths(source_link, target)
            target_link = root / "target-link.jsonl"
            target_link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "target input must not be a symlink"):
                validate_input_paths(source, target_link)

    def test_rejects_same_file_and_non_regular_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text('{"id":"1"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different files"):
                validate_input_paths(source, source)
            with self.assertRaisesRegex(ValueError, "existing regular file"):
                validate_input_paths(source, root / "missing.jsonl")
            with self.assertRaisesRegex(ValueError, "existing regular file"):
                validate_input_paths(source, root)

    def test_jsonl_reports_line_number_for_malformed_and_non_object_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text('{"id":"1"}\n[]\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"rows.jsonl:2: each JSONL line"):
                load_jsonl(path)
            path.write_text('{"id":"1"}\n{bad json}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"rows.jsonl:2: invalid JSONL"):
                load_jsonl(path)


if __name__ == "__main__":
    unittest.main()
