from __future__ import annotations

import unittest
from pathlib import Path


class PlatformWorkbenchUiContractTests(unittest.TestCase):
    def test_blackboard_view_uses_authenticated_platform_apis(self):
        source = (
            Path(__file__).resolve().parents[1] / "platform-ui" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('data-view="blackboard"', source)
        self.assertIn('id="blackboardScopeId"', source)
        self.assertIn('"/blackboard/entries?scope="', source)
        self.assertIn('"/blackboard/entries/"+encodeURIComponent(button.dataset.entryHistory)+"/history"', source)
        self.assertIn('capabilities.blackboard', source)

    def test_sessions_files_and_operations_views_use_real_api_routes(self):
        source = (
            Path(__file__).resolve().parents[1] / "platform-ui" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('data-view="sessions"', source)
        self.assertIn('data-view="files"', source)
        self.assertIn('data-view="operations"', source)
        self.assertIn('api("/sessions?limit=100")', source)
        self.assertIn('api("/files?limit=100")', source)
        self.assertIn('"/quotas/usage?start="', source)
        self.assertIn('"/observability/metrics"', source)
        self.assertIn('api("/audit/tool-events?limit=100")', source)

    def test_ui_keeps_unmounted_capabilities_fail_closed(self):
        source = (
            Path(__file__).resolve().parents[1] / "platform-ui" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("不会调用未实现的占位 API", source)
        self.assertIn("当前服务未挂载", source)


if __name__ == "__main__":
    unittest.main()
