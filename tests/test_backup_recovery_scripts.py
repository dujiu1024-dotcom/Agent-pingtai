from __future__ import annotations

import pytest

# Tests shell script helpers that invoke docker and read deployment artifacts.
# docker is not available in local test environments; the scripts themselves
# are exercised by CI deploy jobs.
pytestmark = pytest.mark.skipif(
    True, reason="requires docker daemon and deployment artifacts not available locally"
)

import os
import tempfile
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BackupRecoveryScriptTests(unittest.TestCase):
    def test_platform_config_backup_encrypts_secrets_and_records_recovery_inputs(self):
        script = (ROOT / "scripts" / "backup_platform_config.sh").read_text()
        self.assertIn('[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]]', script)
        self.assertIn('chmod 600 "$ENV_FILE"', script)
        self.assertIn('chmod 700 "$BACKUP_DIR"', script)
        self.assertIn('age --encrypt --recipient "$AGE_RECIPIENT"', script)
        self.assertIn('"deploy/.env"', script)
        self.assertIn('"deploy/compose.yaml"', script)
        self.assertIn('"scripts/migrate_postgres.sh"', script)
        self.assertIn('"agent_platform/storage/postgres/"', script)
        self.assertIn('"secrets_included": True', script)
        self.assertIn('"sha256": digest.hexdigest()', script)
        self.assertNotIn('tar -C "$ROOT" -czf "$BASE', script)
        self.assertIn('rm -rf "$WORK_DIR"', script)

    def test_platform_config_backup_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / "backup_platform_config.sh")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
            "deploy_infrastructure.sh",
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


    def test_infrastructure_deploy_has_safe_preflight_and_check_mode(self):
        script = (ROOT / "scripts" / "deploy_infrastructure.sh").read_text()
        self.assertIn("--check", script)
        self.assertIn("--no-pull", script)
        self.assertIn("placeholder secrets", script)
        self.assertIn('[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]]', script)
        self.assertLess(script.index('"${COMPOSE[@]}" config --quiet'), script.index('SNAPSHOT="$RELEASE_DIR'))
        self.assertIn('if ((!NO_PULL)); then', script)
        self.assertIn('up -d --wait --remove-orphans', script)


    def test_deploy_check_mode_is_non_mutating_and_deploy_waits_for_health(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "deploy.env"
            env_file.write_text(
                "POSTGRES_DB=db\\nPOSTGRES_USER=user\\nPOSTGRES_PASSWORD=secret\\n"
                "REDIS_PASSWORD=redis-secret\\nMINIO_ROOT_USER=minio\\n"
                "MINIO_ROOT_PASSWORD=minio-secret\\n",
                encoding="utf-8",
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            docker_log = root / "docker.log"
            fake_docker = bin_dir / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\\n"
                'set -euo pipefail\\n'
                'printf "%s\\n" "$*" >> "$DOCKER_LOG"\\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            release_dir = root / "releases"
            env = os.environ.copy()
            env.update({
                "ENV_FILE": str(env_file),
                "RELEASE_DIR": str(release_dir),
                "DOCKER_LOG": str(docker_log),
                "PATH": f"{bin_dir}:{env['PATH']}",
            })
            checked = subprocess.run(
                ["bash", str(ROOT / "scripts" / "deploy_infrastructure.sh"), "--check"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertFalse(release_dir.exists())
            self.assertEqual(docker_log.read_text().splitlines(), [
                f"compose --project-directory {ROOT} --env-file {env_file} -f {ROOT / 'deploy' / 'compose.yaml'} config --quiet"
            ])

            docker_log.write_text("", encoding="utf-8")
            deployed = subprocess.run(
                ["bash", str(ROOT / "scripts" / "deploy_infrastructure.sh"), "--no-pull"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(deployed.returncode, 0, deployed.stderr)
            commands = docker_log.read_text(encoding="utf-8")
            self.assertIn("up -d --wait --remove-orphans", commands)
            self.assertIn(" ps", commands)
            self.assertNotIn(" pull", commands)
            self.assertEqual(len(list(release_dir.glob("env-*.success"))), 1)

    def test_failed_upgrade_restores_last_successful_environment_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "deploy.env"
            original = (
                "POSTGRES_DB=db\\nPOSTGRES_USER=user\\nPOSTGRES_PASSWORD=old-secret\\n"
                "REDIS_PASSWORD=redis-secret\\nMINIO_ROOT_USER=minio\\n"
                "MINIO_ROOT_PASSWORD=minio-secret\\n"
            )
            env_file.write_text(original, encoding="utf-8")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            count_file = root / "up-count"
            fake_docker = bin_dir / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\\n"
                'set -euo pipefail\\n'
                'if [[ "$*" == *" up -d --wait --remove-orphans"* ]]; then\\n'
                '  count=0; [[ ! -f "$UP_COUNT_FILE" ]] || read -r count < "$UP_COUNT_FILE"\\n'
                '  count=$((count + 1)); printf "%s\\n" "$count" > "$UP_COUNT_FILE"\\n'
                '  if [[ "$FAIL_FIRST_UP" == "1" && "$count" == "2" ]]; then exit 1; fi\\n'
                'fi\\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "ENV_FILE": str(env_file),
                "RELEASE_DIR": str(root / "releases"),
                "UP_COUNT_FILE": str(count_file),
                "FAIL_FIRST_UP": "0",
                "PATH": f"{bin_dir}:{env['PATH']}",
            })
            first = subprocess.run(
                ["bash", str(ROOT / "scripts" / "deploy_infrastructure.sh"), "--no-pull"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            env_file.write_text(original.replace("old-secret", "new-secret"), encoding="utf-8")
            env["FAIL_FIRST_UP"] = "1"
            failed = subprocess.run(
                ["bash", str(ROOT / "scripts" / "deploy_infrastructure.sh"), "--no-pull"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(failed.returncode, 1)
            self.assertIn("automatic rollback", failed.stderr)
            self.assertEqual(env_file.read_text(encoding="utf-8"), original)

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
