import tempfile
import unittest
from pathlib import Path

from scripts.ops_runbook import OPERATIONS, preflight, validate_backup_manifest


class OperationsRunbookTests(unittest.TestCase):
    def test_lifecycle_operations_have_ordered_steps(self):
        self.assertEqual(set(OPERATIONS), {
            "deploy", "upgrade", "backup", "restore", "rollback", "verify", "diagnose"
        })
        for steps in OPERATIONS.values():
            self.assertGreaterEqual(len(steps), 4)
            self.assertTrue(all(isinstance(step, str) and step for step in steps))

    def test_complete_backup_manifest_passes(self):
        manifest = {
            "backup_id": "BKP-2026-001", "created_at": "2026-11-17T04:00:00Z",
            "source_release": "sha256:release",
            "artifacts": [
                {"name": name, "sha256": "a" * 64, "location": f"vault://backup/{name}"}
                for name in ("postgres", "redis", "objects", "configuration")
            ],
            "components": {
                "postgres": True, "redis": True, "objects": True, "configuration": True
            },
        }
        self.assertEqual(validate_backup_manifest(manifest), [])

    def test_manifest_rejects_naive_time_bad_checksum_and_missing_components(self):
        manifest = {"backup_id": "BKP-1", "created_at": "2026-11-17T04:00:00",
            "source_release": "v1",
            "artifacts": [{"name": "postgres", "sha256": "bad", "location": "vault://pg"}],
            "components": {"postgres": True}}
        errors = validate_backup_manifest(manifest)
        self.assertTrue(any("timezone" in error for error in errors))
        self.assertTrue(any("sha256" in error for error in errors))
        self.assertTrue(any("redis" in error for error in errors))
        self.assertTrue(any("objects" in error for error in errors))
        self.assertTrue(any("configuration" in error for error in errors))

    def test_preflight_never_emits_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("TOKEN=do-not-print", encoding="utf-8")
            (root / ".env").chmod(0o600)
            result = preflight(root)
        self.assertTrue(result["env_file_present"])
        self.assertFalse(result["secret_values_emitted"])
        self.assertNotIn("do-not-print", str(result))


if __name__ == "__main__":
    unittest.main()
