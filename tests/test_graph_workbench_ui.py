from pathlib import Path


def test_graph_operations_view_uses_authenticated_graph_api_contract():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert 'data-view="graph"' in html
    assert 'graph:["graph"]' in script
    assert '"/graph/entities?q="' in script
    assert '"/graph/metrics?q="' in script
    assert '"/graph/entities/"+encodeURIComponent' in script
    assert 'data-graph-correct' in script
    assert "授权搜索样本" in script
    assert "LightRAG Server" in script
    assert "graph_visualization" in script
    assert "<iframe" in script
    assert "graph_annotation" in script
    assert '"graph_annotation": graph_governance_resolver is not None' in (Path(__file__).resolve().parents[1] / "agent_platform" / "api" / "app.py").read_text(encoding="utf-8")
