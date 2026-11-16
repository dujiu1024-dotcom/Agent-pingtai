from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_legacy_sqlite import export_database
from scripts.verify_legacy_export import verify_export


class LegacyExportVerificationTests(unittest.TestCase):
    def _export(self, root: Path) -> Path:
        import sqlite3

        database = root / "legacy.db"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO records VALUES (1, 'safe')")
        connection.commit()
        connection.close()
        output = root / "export"
        export_database(database, output)
        return output

    def test_verifies_valid_export(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self._export(Path(directory))
            report = verify_export(output)
            self.assertTrue(report["valid"])
            self.assertEqual(report["rows"], 1)

    def test_rejects_non_object_manifest(self):
        for payload in ("[]", '"manifest"', "null", "42"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                output = self._export(Path(directory))
                (output / "manifest.json").write_text(payload, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "manifest must be a JSON object"):
                    verify_export(output)

    def test_rejects_symlinked_export_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = self._export(root)
            exported = output / "records.jsonl"
            external = root / "external.jsonl"
            external.write_bytes(exported.read_bytes())
            exported.unlink()
            try:
                exported.symlink_to(external)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks are unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "non-symlink"):
                verify_export(output)

    def test_rejects_symlinked_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = self._export(root)
            manifest = output / "manifest.json"
            external = root / "manifest-copy.json"
            external.write_bytes(manifest.read_bytes())
            manifest.unlink()
            try:
                manifest.symlink_to(external)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks are unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "manifest"):
                verify_export(output)

    def test_rejects_symlinked_export_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = self._export(root)
            link = root / "export-link"
            try:
                link.symlink_to(output, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks are unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "export directory"):
                verify_export(link)

    def test_rejects_duplicate_and_reserved_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self._export(Path(directory))
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["tables"][0]["file"] = "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reserved"):
                verify_export(output)


if __name__ == "__main__":
    unittest.main()
