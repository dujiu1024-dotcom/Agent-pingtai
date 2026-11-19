from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ApprovalEvaluationWorkbenchContractTests(unittest.TestCase):
    def test_evaluation_dataset_repository_is_wired_through_platform_app(self):
        source = (ROOT / "agent_platform" / "api" / "app.py").read_text(encoding="utf-8")
        self.assertIn("evaluation_dataset_repository: Any | None = None", source)
        self.assertIn("evaluation_dataset_repository=evaluation_dataset_repository", source)
        self.assertIn('"evaluation_datasets": evaluation_dataset_repository is not None', source)
        self.assertIn("evaluation_dataset_repository, evaluation_run_service", source)
        self.assertIn("evaluation manager authorizer must be configured together", source)

    def test_approval_view_uses_server_digest_and_supported_decision_routes(self):
        source = (ROOT / "platform-ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('api("/approvals/"+encodeURIComponent(id))', source)
        self.assertIn('api("/approvals/"+encodeURIComponent(id)+"/decision"', source)
        self.assertIn('api("/approvals/"+encodeURIComponent(id)+"/revoke"', source)
        self.assertIn("action_hash", source)
        self.assertIn("不返回原始参数快照", source)
        self.assertIn("risk_level", source)
        self.assertIn("requested_by", source)
        self.assertIn("expires_at", source)

    def test_dataset_workbench_uses_versioned_evaluation_api_contract(self):
        source = (ROOT / "platform-ui" / "index.html").read_text(encoding="utf-8")
        for route in (
            "/observability/evaluations/datasets/import",
            "/observability/evaluations/datasets/",
            "/observability/evaluations/run",
            "/observability/evaluations?dataset_id=",
            "/observability/evaluations/",
        ):
            self.assertIn(route, source)
        self.assertIn("evaluation_datasets", source)
        self.assertIn("evaluation_runs", source)
        self.assertIn("evaluation_reports", source)
        self.assertIn("dataset_version", source)
        self.assertIn("eval_run_ids", source)
        self.assertIn("source_ref", source)


if __name__ == "__main__":
    unittest.main()
