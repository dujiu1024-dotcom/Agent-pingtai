from __future__ import annotations

import unittest
from pathlib import Path


class ApprovalWorkbenchUiContractTests(unittest.TestCase):
    def test_workbench_has_detail_decision_revoke_and_csrf_contract(self):
        html = (Path(__file__).resolve().parents[1] / "platform-ui" / "index.html").read_text()
        self.assertIn('id="approvalList"', html)
        self.assertIn('id="approvalDetail"', html)
        self.assertIn("data-approval-action='detail'", html)
        self.assertIn("data-approval-action='approve'", html)
        self.assertIn("data-approval-action='reject'", html)
        self.assertIn("data-approval-action='revoke'", html)
        self.assertIn('"/decision"', html)
        self.assertIn('"/revoke"', html)
        self.assertIn('"X-CSRF-Token":csrf', html)
        self.assertIn("encodeURIComponent(id)", html)


if __name__ == "__main__":
    unittest.main()
