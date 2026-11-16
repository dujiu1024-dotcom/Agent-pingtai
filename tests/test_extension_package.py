from __future__ import annotations

import hashlib
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_platform.extensions.package import (
    extract_extension_zip,
    verify_extension_signature,
)


class ExtensionPackageExtractionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.archive = self.root / "extension.zip"
        self.destination = self.root / "installed" / "extension"

    def _write_zip(self, entries):
        with zipfile.ZipFile(self.archive, "w") as package:
            for name, content in entries:
                package.writestr(name, content)

    def test_extracts_files_with_owner_only_permissions_and_digest(self):
        self._write_zip([("manifest.json", b'{"id":"sample-extension"}'),
                         ("plugin/main.py", b"def run(): return 1")])
        report = extract_extension_zip(self.archive, self.destination)
        self.assertEqual(report.files_extracted, 2)
        self.assertEqual(report.uncompressed_bytes, 44)
        self.assertEqual(len(report.archive_sha256), 64)
        self.assertEqual(stat.S_IMODE((self.destination / "plugin/main.py").stat().st_mode),
                         0o600)

    def test_rejects_traversal_absolute_and_drive_paths(self):
        for name in ("../escape.py", "/tmp/escape.py", "C:/escape.py", "plugin/../escape.py"):
            with self.subTest(name=name):
                self._write_zip([(name, b"bad")])
                with self.assertRaisesRegex(ValueError, "unsafe|absolute|drive"):
                    extract_extension_zip(self.archive, self.destination)
                self.assertFalse(self.destination.exists())

    def test_rejects_symlinks_duplicate_paths_and_case_collisions(self):
        with zipfile.ZipFile(self.archive, "w") as package:
            symlink = zipfile.ZipInfo("plugin/link")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(symlink, "target")
        with self.assertRaisesRegex(ValueError, "symlink"):
            extract_extension_zip(self.archive, self.destination)

        self._write_zip([("Plugin/main.py", b"a"), ("plugin/MAIN.py", b"b")])
        with self.assertRaisesRegex(ValueError, "case-colliding"):
            extract_extension_zip(self.archive, self.destination)

    def test_enforces_file_and_uncompressed_size_limits(self):
        self._write_zip([("one.txt", b"1234"), ("two.txt", b"5678")])
        with self.assertRaisesRegex(ValueError, "limits"):
            extract_extension_zip(self.archive, self.destination, max_files=1)
        with self.assertRaisesRegex(ValueError, "limits"):
            extract_extension_zip(self.archive, self.destination, max_uncompressed_bytes=4)


    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for Ed25519 test")
    def test_verifies_detached_ed25519_signature_and_binds_extraction_digest(self):
        self._write_zip([("manifest.json", b'{"id":"signed-extension"}')])
        private_key = self.root / "publisher-private.pem"
        public_key = self.root / "publisher-public.pem"
        digest_file = self.root / "archive.sha256"
        signature_file = self.root / "archive.sig"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private_key)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
            check=True, capture_output=True,
        )
        archive_digest = hashlib.sha256(self.archive.read_bytes()).digest()
        digest_file.write_bytes(archive_digest)
        subprocess.run(
            ["openssl", "pkeyutl", "-sign", "-inkey", str(private_key),
             "-rawin", "-in", str(digest_file), "-out", str(signature_file)],
            check=True, capture_output=True,
        )
        verified = verify_extension_signature(
            self.archive, signature_file.read_bytes(), public_key.read_bytes(),
        )
        report = extract_extension_zip(
            self.archive, self.destination, expected_archive_sha256=verified,
        )
        self.assertEqual(report.archive_sha256, verified)

        tampered = self.root / "tampered.zip"
        tampered.write_bytes(self.archive.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            verify_extension_signature(
                tampered, signature_file.read_bytes(), public_key.read_bytes(),
            )

    def test_extraction_rejects_digest_changed_after_signature_verification(self):
        self._write_zip([("main.py", b"safe")])
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            extract_extension_zip(
                self.archive, self.destination, expected_archive_sha256="0" * 64,
            )
        self.assertFalse(self.destination.exists())

    def test_rejects_existing_destination_and_archive_symlink(self):
        self._write_zip([("main.py", b"ok")])
        self.destination.parent.mkdir(parents=True)
        self.destination.mkdir()
        with self.assertRaises(FileExistsError):
            extract_extension_zip(self.archive, self.destination)


if __name__ == "__main__":
    unittest.main()
