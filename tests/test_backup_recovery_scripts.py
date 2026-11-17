from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BackupRecoveryScriptTests(unittest.TestCase):
    def test_backup_requires_encryption_and_checksum(self):
        script = (ROOT / "scripts" / "backup_postgres.sh").read_text()
        self.assertIn("PG_DSN", script)
        self.assertIn("AGE_RECIPIENT", script)
        self.assertIn("age --encrypt", script)
        self.assertIn("sha256sum", script)
        self.assertIn("chmod 700", script)

    def test_restore_requires_explicit_confirmation_and_checksum(self):
        script = (ROOT / "scripts" / "restore_postgres.sh").read_text()
        self.assertIn("AGE_IDENTITY", script)
        self.assertIn("CONFIRM_RESTORE", script)
        self.assertIn("RESTORE-PLATFORM-DATABASE", script)
        self.assertIn("sha256sum --check", script)
        self.assertIn("age --decrypt", script)
        self.assertIn("pg_restore --dbname", script)

    def test_postgres_restore_validates_before_destructive_restore(self):
        script = (ROOT / "scripts" / "restore_postgres.sh").read_text()
        self.assertIn("manifest checksum mismatch", script)
        self.assertIn("pg_restore --list", script)
        self.assertIn("--single-transaction", script)
        self.assertIn("--clean --if-exists", script)
        self.assertIn("ALLOW_DIFFERENT_DATABASE", script)
        self.assertLess(script.index("sha256sum --check"), script.index("pg_restore --dbname"))
        self.assertLess(script.index("pg_restore --list"), script.index("pg_restore --dbname"))

    def test_postgres_backup_manifest_is_stream_hashed_and_published_last(self):
        backup = (ROOT / "scripts" / "backup_postgres.sh").read_text()
        restore = (ROOT / "scripts" / "restore_postgres.sh").read_text()
        self.assertIn("manifest.json", backup)
        self.assertIn("pg_dump --version", backup)
        self.assertIn('mv "$MANIFEST_TMP"', backup)
        self.assertIn("for chunk in iter(lambda: stream.read(1024 * 1024), b\"\")", backup)
        self.assertIn("for chunk in iter(lambda: stream.read(1024 * 1024), b\"\")", restore)
        self.assertNotIn("path.read_bytes()", backup)
        self.assertNotIn("artifact.read_bytes()", restore)

    def test_redis_restore_requires_stopped_service_and_preserves_previous_rdb(self):
        script = (ROOT / "scripts" / "restore_redis.sh").read_text()
        self.assertIn("REDIS_STOPPED_CONFIRM", script)
        self.assertIn('[[ "$REDIS_STOPPED_CONFIRM" == "YES" ]]', script)
        self.assertIn('[[ -d "$REDIS_DATA_DIR" && ! -L "$REDIS_DATA_DIR" ]]', script)
        self.assertIn('[[ ! -L "$TARGET" ]]', script)
        self.assertIn("redis-check-rdb", script)
        self.assertIn('PREVIOUS="$TARGET.pre-restore.', script)
        self.assertLess(script.index("redis-check-rdb"), script.index('mv "$WORK_DIR/dump.rdb" "$TARGET"'))

    def test_object_restore_rejects_unsafe_archive_and_delete_extra_is_opt_in(self):
        script = (ROOT / "scripts" / "restore_object_storage.sh").read_text()
        self.assertIn("path.is_absolute() or \"..\" in path.parts", script)
        self.assertIn("member.issym() or member.islnk()", script)
        self.assertIn("target.resolve().is_relative_to(root)", script)
        self.assertIn('if [[ "${OBJECT_RESTORE_DELETE_EXTRA:-0}" == "1" ]]', script)
        self.assertIn("mc mirror --preserve --overwrite --remove", script)
        self.assertIn("mc mirror --preserve --overwrite", script)
        self.assertLess(script.index("sha256sum --check"), script.index('mc mirror --preserve --overwrite'))

    def test_all_shell_scripts_parse_without_syntax_errors(self):
        for name in (
            "backup_postgres.sh",
            "restore_postgres.sh",
            "backup_redis.sh",
            "restore_redis.sh",
            "backup_object_storage.sh",
            "restore_object_storage.sh",
            "migrate_postgres.sh",
        ):
            with self.subTest(script=name):
                result = subprocess.run(
                    ["bash", "-n", str(ROOT / "scripts" / name)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_infrastructure_deploy_rolls_back_only_to_successful_snapshot(self):
        script = (ROOT / "scripts" / "deploy_infrastructure.sh").read_text()
        self.assertIn("env-*.success", script)
        self.assertIn("previous successful deployment snapshot", script)
        self.assertIn("automatic rollback", script)
        self.assertIn('printf \'%s\\n\' "$SNAPSHOT" > "$SNAPSHOT.success"', script)

    def test_migration_runner_expands_required_environment_and_file_list(self):
        script = (ROOT / "scripts" / "migrate_postgres.sh").read_text()
        self.assertIn(': "${PG_DSN:', script)
        self.assertIn('files=("$MIGRATION_DIR"/*.sql)', script)
        self.assertNotIn(r'\${PG_DSN', script)
        self.assertNotIn(r'\${#files[@]}', script)

    def test_migration_runner_is_checksummed_and_serialized(self):
        script = (ROOT / "scripts" / "migrate_postgres.sh").read_text()
        self.assertIn("pg_advisory_lock", script)
        self.assertIn("platform_schema_migrations", script)
        self.assertIn("checksum_matches", script)
        self.assertIn("ON_ERROR_STOP", script)


if __name__ == "__main__":
    unittest.main()
