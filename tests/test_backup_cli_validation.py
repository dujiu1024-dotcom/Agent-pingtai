from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_platform_backup


class BackupCliValidationTests(unittest.TestCase):
    def test_dry_run_reports_plan_without_running_backup_or_creating_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backup_dir = Path(temporary) / "not-created"
            output = io.StringIO()
            with patch.object(run_platform_backup, "run_backup_set") as run_backup:
                with contextlib.redirect_stdout(output):
                    result = run_platform_backup.main([
                        "--backup-dir", str(backup_dir), "--dry-run",
                    ])

            self.assertEqual(result, 0)
            run_backup.assert_not_called()
            self.assertFalse(backup_dir.exists())
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "dry-run")
            self.assertEqual(
                payload["components"],
                ["postgres", "redis", "object-storage", "config"],
            )
            self.assertIn("no files or directories created", payload["execution"])

    def test_empty_backup_directory_is_rejected(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            result = run_platform_backup.main(["--backup-dir", " ", "--dry-run"])
        self.assertEqual(result, 1)
        self.assertIn("--backup-dir must not be empty", errors.getvalue())

    def test_existing_file_cannot_be_used_as_backup_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "not-a-directory"
            target.write_text("fixture", encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                result = run_platform_backup.main([
                    "--backup-dir", str(target), "--dry-run",
                ])
        self.assertEqual(result, 1)
        self.assertIn("must be a directory", errors.getvalue())

    def test_symlink_backup_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real"
            target.mkdir()
            link = root / "linked"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks unavailable")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                result = run_platform_backup.main([
                    "--backup-dir", str(link), "--dry-run",
                ])
        self.assertEqual(result, 1)
        self.assertIn("must not be a symlink", errors.getvalue())

    def test_dry_run_rejects_invalid_path_without_invoking_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "file"
            target.write_text("fixture", encoding="utf-8")
            with patch.object(run_platform_backup, "run_backup_set") as run_backup:
                with contextlib.redirect_stderr(io.StringIO()):
                    result = run_platform_backup.main([
                        "--backup-dir", str(target), "--dry-run",
                    ])
            self.assertEqual(result, 1)
            run_backup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
