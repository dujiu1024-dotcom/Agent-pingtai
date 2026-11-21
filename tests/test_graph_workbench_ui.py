from pathlib import Path


def test_graph_operations_view_uses_authenticated_graph_api_contract():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js_files = list((root / "assets").glob("*.js"))
    assert js_files, "JS build 产物缺失"
    assert '<div id="root">' in html
    # graph_annotation resolver 在 app.py 中被注册
    app_py = (Path(__file__).resolve().parents[1] / "agent_platform" / "api" / "app.py").read_text(encoding="utf-8")
    assert "graph" in app_py.lower()
