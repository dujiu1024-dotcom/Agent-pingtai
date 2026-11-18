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
        self.assertIn('data-view="evaluation"', source)
        self.assertIn('api("/knowledge/evaluate"', source)
        self.assertIn('data-memory-action=\'archive\'', source)
        self.assertIn('api("/knowledge/search?q="+encodeURIComponent', source)

    def test_ui_keeps_unmounted_capabilities_fail_closed(self):
        source = (
            Path(__file__).resolve().parents[1] / "platform-ui" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("不会调用未实现的占位 API", source)
        self.assertIn("当前服务未挂载", source)

    def test_memory_governance_exposes_provenance_versions_and_safe_unknown_states(self):
        source = (
            Path(__file__).resolve().parents[1] / "platform-ui" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="memoryFilter"', source)
        self.assertIn('data-memory-action=\'detail\'', source)
        self.assertIn('x.source_ref', source)
        self.assertIn('x.scope_id', source)
        self.assertIn('x.version', source)
        self.assertIn('服务端返回未知状态', source)
        self.assertIn('最近版本内容差异', source)
        self.assertIn('api("/memories")', source)
        self.assertIn('api("/memories/"+id+"/history")', source)
        self.assertIn('data-memory-action=\'confirm\'', source)


    def test_session_workbench_supports_cursor_history_exports_and_attachments(self):
        source = (
            Path(__file__).resolve().parents[1] / "platform-ui" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="sessionFilter"', source)
        self.assertIn('include_archived', source)
        self.assertIn('before_message_id', source)
        self.assertIn('/export?format=markdown', source)
        self.assertIn('asset_ids:assetIds', source)
        self.assertIn('credentials:"same-origin"', source)
        self.assertIn('sessionMessages").addEventListener("click"', source)
        self.assertIn('await show("files")', source)

    def test_file_workbench_uses_authorized_metadata_versions_restore_and_delete(self):
        source = (
            Path(__file__).resolve().parents[1] / "platform-ui" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="fileFilterSession"', source)
        self.assertIn('api("/files/"+id+"/metadata")', source)
        self.assertIn('api("/files/"+id+"/versions")', source)
        self.assertIn('/restore?expected_version=', source)
        self.assertIn('api("/files/"+id,{method:"DELETE"})', source)
        self.assertIn('downloadResponse("/files/"+id+"/versions/', source)


if __name__ == "__main__":
    unittest.main()
