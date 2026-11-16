from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from agent_platform.operations.object_backup import (
    stream_object_to_file, write_backup_manifest,
)


class ObjectBackupTests(unittest.TestCase):
    def test_streams_and_records_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = stream_object_to_file(io.BytesIO(b"payload"), root, key="tenant/a.bin")
            self.assertEqual(entry.size_bytes, 7)
            self.assertEqual((root / "tenant" / "a.bin").read_bytes(), b"payload")
            manifest = root / "manifest.json"
            write_backup_manifest(manifest, [entry])
            self.assertIn(entry.sha256, manifest.read_text())

    def test_rejects_unsafe_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                stream_object_to_file(io.BytesIO(b"x"), Path(directory), key="../escape")
            entry = stream_object_to_file(io.BytesIO(b"x"), Path(directory), key="a")
            with self.assertRaises(ValueError):
                write_backup_manifest(Path(directory) / "m.json", [entry, entry])
