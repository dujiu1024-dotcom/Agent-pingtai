from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workbench_exposes_explicit_legacy_panel_compatibility_entry():
    html = (ROOT / "agent_platform/web/static/index.html").read_text(encoding="utf-8")
    assert 'data-legacy-compat' in html
    assert 'href="/app/?legacy=1"' in html
    assert "旧版功能兼容入口" in html


def test_legacy_bridge_is_not_misrepresented_as_native_feature_parity():
    docs = (ROOT / "docs/switch/issue-667-a-667-05-legacy-panel-bridge.md").read_text(encoding="utf-8")
    assert "兼容入口不等于功能迁移完成" in docs
    assert "业务、内容和 roundtable" in docs
    assert "A-667-05 继续保持未验收" in docs
