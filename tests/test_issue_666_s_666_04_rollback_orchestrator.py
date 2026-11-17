from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.rollback_cutover import REQUIRED_STEPS, load_plan, run_plan


class CutoverRollbackOrchestratorTests(unittest.TestCase):
    def _plan(self, directory: Path, fail_step: str | None = None) -> Path:
        log = directory / "commands.log"
        steps = []
        for name in REQUIRED_STEPS:
            code = (
                "from pathlib import Path; Path(" + repr(str(log))
                + ").open('a').write(" + repr(name + "\n") + ")"
            )
            if name == fail_step:
                code += "; raise SystemExit(9)"
            steps.append({
                "name": name, "argv": [sys.executable, "-c", code],
                "timeout_seconds": 10,
            })
        path = directory / "plan.json"
        path.write_text(json.dumps({
            "version": 1, "max_duration_seconds": 30, "steps": steps,
        }), encoding="utf-8")
        return path

    def test_plan_requires_all_steps_in_safety_order(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._plan(Path(temp))
            plan = load_plan(path)
            self.assertEqual(tuple(step["name"] for step in plan["steps"]), REQUIRED_STEPS)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["steps"].reverse()
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "safety order"):
                load_plan(path)

    def test_success_runs_every_step_and_writes_auditable_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plan = load_plan(self._plan(directory))
            evidence = directory / "evidence.json"
            self.assertEqual(run_plan(plan, evidence), 0)
            report = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "succeeded")
            self.assertEqual(report["completed_steps"], 6)
            self.assertEqual([step["name"] for step in report["steps"]], list(REQUIRED_STEPS))
            self.assertEqual(
                (directory / "commands.log").read_text(encoding="utf-8").splitlines(),
                list(REQUIRED_STEPS),
            )

    def test_failure_stops_before_route_switch_and_keeps_writes_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plan = load_plan(self._plan(directory, fail_step="verify_parity"))
            evidence = directory / "evidence.json"
            self.assertEqual(run_plan(plan, evidence), 1)
            report = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "step_failed")
            self.assertEqual(report["completed_steps"], 3)
            self.assertEqual(
                (directory / "commands.log").read_text(encoding="utf-8").splitlines(),
                ["freeze_writes", "reverse_sync", "verify_parity"],
            )
            self.assertIn("not automatically lifted", report["safety_note"])

    def test_timeout_stops_following_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plan_path = self._plan(directory)
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            payload["steps"][1]["argv"] = [sys.executable, "-c", "import time; time.sleep(2)"]
            payload["steps"][1]["timeout_seconds"] = 1
            plan_path.write_text(json.dumps(payload), encoding="utf-8")
            evidence = directory / "evidence.json"
            self.assertEqual(run_plan(load_plan(plan_path), evidence), 1)
            report = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "step_timed_out")
            self.assertEqual(report["completed_steps"], 2)
            self.assertEqual(report["steps"][-1]["status"], "timed_out")

    def test_execution_refuses_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plan = load_plan(self._plan(directory))
            evidence = directory / "existing.json"
            evidence.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be new"):
                run_plan(plan, evidence)
            self.assertFalse((directory / "commands.log").exists())


if __name__ == "__main__":
    unittest.main()
