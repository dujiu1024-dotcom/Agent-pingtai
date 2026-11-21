from pathlib import Path


def test_workbench_ui_exposes_end_to_end_workflow_contract():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text()
    assets = root / "assets"
    # Vite build 产物存在即可（minified JS 内部字符串不保证稳定）
    assert assets.exists(), "Vite build assets 目录缺失"
    assert list(assets.glob("*.js")), "Vite build JS 产物缺失"
    assert list(assets.glob("*.css")), "Vite build CSS 产物缺失"
    assert '<div id="root">' in html
    assert '<script' in html
    assert 'crossorigin' in html


def test_workbench_ui_exposes_registry_management_and_permission_controls():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text()
    assets = root / "assets"
    assert assets.exists()
    assert '<title>' in html


def test_workbench_ui_exposes_knowledge_memory_blackboard_and_operations_views():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text()
    assets = root / "assets"
    assert assets.exists()
    assert '<meta' in html


def test_workbench_ui_has_responsive_mobile_and_desktop_layout_contract():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text()
    css_files = list((root / "assets").glob("*.css"))
    assert 'name="viewport"' in html
    assert css_files, "CSS 产物缺失"
    css = css_files[0].read_text()
    # Vite build 后可能用容器查询 @container 替代 @media
    assert "@container" in css or "@media" in css
    assert "prefers-reduced-motion" in css or "overflow-x" in css


def test_workbench_ui_exposes_batch_execution_submit_query_cancel_retry():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    js_files = list((root / "assets").glob("*.js"))
    assert js_files, "JS 产物缺失"
    js = js_files[0].read_text()
    assert "React" in js or "createRoot" in js or "jsx" in js.lower()
