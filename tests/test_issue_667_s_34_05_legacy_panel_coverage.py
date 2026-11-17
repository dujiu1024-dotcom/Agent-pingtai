from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_new_workbench_exposes_legacy_feature_index():
    html = (ROOT / "agent_platform/web/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
    assert 'data-view="legacy-panels"' in html
    for panel in ("ops", "health", "business", "content", "roundtable"):
        assert f"legacy(x[3])" in script
        assert f'"{panel}"' in script
    assert 'btn("打开新工作台入口","data-go=\'' in script
    for native in ("tasks", "artifacts", "observability"):
        assert f'"{native}"' in script


def test_legacy_links_are_direct_and_only_use_allowlisted_panel_names():
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
    legacy = (ROOT / "console.html").read_text(encoding="utf-8")
    assert '"/app/?legacy=1&panel="+encodeURIComponent(panel)' in script
    assert "function openLegacyPanelFromQuery()" in legacy
    assert "new URLSearchParams(window.location.search).get('panel')" in legacy
    for handler in ("openOps", "openHealth", "openBiz", "openContent", "openRoundtable"):
        assert handler in legacy
    assert "load().then(openLegacyPanelFromQuery)" in legacy


def test_legacy_panel_index_discloses_unmigrated_backend_boundary():
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
    assert "尚未迁移为新 API" in script
    assert "商务、内容日历、圆桌会议和旧版系统自检目前仍由 legacy console 提供" in script
    assert 'target="_blank" rel="noopener"' in script
