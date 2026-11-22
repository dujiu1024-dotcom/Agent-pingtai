from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPOSITORY_ROOT / "scripts" / "downgrade_postgres.sh"


class DowngradePostgresRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.migrations = self.root / "migrations"
        self.migrations.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.capture = self.root / "psql-input.sql"
        fake_psql = self.bin / "psql"
        fake_psql.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "while [[ $# -gt 0 ]]; do\n"
            "  if [[ \"$1\" == \"-f\" ]]; then cat \"$2\" > \"$CAPTURE_PSQL_INPUT\"; exit 0; fi\n"
            "  shift\n"
            "done\n"
            "exit 9\n",
            encoding="utf-8",
        )
        fake_psql.chmod(0o755)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_runner(self, migration_name: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.bin}{os.pathsep}{env.get('PATH', '')}",
            "PG_DSN": "postgresql://not-used",
            "MIGRATION_DIR": str(self.migrations),
            "CAPTURE_PSQL_INPUT": str(self.capture),
        })
        return subprocess.run(
            ["bash", str(RUNNER), migration_name],
            cwd=REPOSITORY_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def add_down(self, name: str, sql: str = "DROP TABLE sample;\n") -> None:
        (self.migrations / f"{name}.down.sql").write_text(sql, encoding="utf-8")

    def assert_selected_exactly(self, selected: str, other: str) -> None:
        self.add_down(selected, f"SELECT '{selected}';\n")
        self.add_down(other, f"SELECT '{other}';\n")

        result = self.run_runner(selected)

        self.assertEqual(result.returncode, 0, result.stderr)
        generated = self.capture.read_text(encoding="utf-8")
        self.assertIn(f"migration_name='{selected}'", generated)
        self.assertNotIn(f"migration_name='{other}'", generated)

    def test_reads_exact_down_file_and_deletes_exact_ledger_name(self) -> None:
        name = "025_workbench_task_execution.sql"
        self.add_down(name, "BEGIN;\nDROP TABLE sample;\nCOMMIT;\n")

        result = self.run_runner(name)

        self.assertEqual(result.returncode, 0, result.stderr)
        generated = self.capture.read_text(encoding="utf-8")
        self.assertIn(f"migration_name='{name}'", generated)
        self.assertIn(f"DELETE FROM platform_schema_migrations WHERE migration_name='{name}'", generated)

    def test_disambiguates_025_and_031_full_filenames(self) -> None:
        self.assert_selected_exactly("025_workbench_task_execution.sql", "031_workbench_task_execution.sql")

    def test_disambiguates_044_and_054_full_filenames(self) -> None:
        self.assert_selected_exactly("044_workbench_task_priority.sql", "054_workbench_task_priority.sql")

    def test_disambiguates_045_and_056_full_filenames(self) -> None:
        self.assert_selected_exactly("045_workbench_task_priority.sql", "056_workbench_task_retry.sql")

    def test_requires_explicit_down_file(self) -> None:
        result = self.run_runner("044_workbench_task_priority.sql")
        self.assertEqual(result.returncode, 2)
        self.assertIn("down migration not found", result.stderr)

    def test_rejects_prefix_only_and_path_traversal_arguments(self) -> None:
        for invalid in ("025", "../025_workbench_task_execution.sql", "025_bad-name.sql"):
            with self.subTest(invalid=invalid):
                result = self.run_runner(invalid)
                self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
