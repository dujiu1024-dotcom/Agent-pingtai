from pathlib import Path


def test_workbench_ui_exposes_end_to_end_workflow_contract():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text()
    script = (root / "app.js").read_text()
    for view in ("sessions", "tasks", "approvals", "artifacts"):
        assert f'data-view="{view}"' in html
    for route in ('/sessions', '/files', '/workbench/tasks', '/runs/', '/approvals', '/decision'):
        assert route in script
    assert 'X-CSRF-Token' in script
    assert 'data-task-retry' in script
    assert 'data-task-cancel' in script
    assert 'data-download' in script


def test_workbench_ui_exposes_registry_management_and_permission_controls():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text()
    script = (root / "app.js").read_text()
    assert 'data-view="registry"' in html
    for kind in ("agent", "workflow", "model_provider", "model_template", "tool", "skill"):
        assert f'"{kind}"' in script
    assert '"/registry/"' in script
    assert '"/versions"' in script
    assert '"/publish"' in script
    assert '"/disable"' in script
    assert "Registry Authorizer" in script
    assert "Secret Reference" in script
