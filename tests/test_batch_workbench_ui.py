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


def test_workbench_ui_exposes_knowledge_memory_blackboard_and_operations_views():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text()
    script = (root / "app.js").read_text()
    assert 'data-view="governance"' in html
    for route in (
        "/knowledge/documents", "/memories/candidates", "/memories",
        "/blackboard/entries", "/observability/evaluations",
        "/observability/alerts", "/observability/metrics",
        "/quotas/usage", "/audit/tool-events", "/audit/identity-events",
    ):
        assert route in script
    for label in ("知识库管理", "记忆管理", "Blackboard 协作视图", "评测报告",
                  "成本与配额", "运营告警", "审计追踪"):
        assert label in script
    assert "30*86400000" in script
    assert "Promise.allSettled" in script


def test_workbench_ui_has_responsive_mobile_and_desktop_layout_contract():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    html = (root / "index.html").read_text()
    css = (root / "style.css").read_text()
    script = (root / "app.js").read_text()
    assert 'name="viewport"' in html
    assert "@media(max-width:900px)" in css
    assert "@media(max-width:640px)" in css
    assert "@media(max-width:380px)" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "overflow-x:auto" in css
    assert "prefers-reduced-motion:reduce" in css
    assert "applyCapabilityGate" in script
    assert "VIEW_CAPABILITIES" in script
    assert "nav=function(view)" in script
    assert "credentials:\"same-origin\"" in script
    assert "X-CSRF-Token" in script


def test_workbench_ui_exposes_batch_execution_submit_query_cancel_retry():
    root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
    script = (root / "app.js").read_text()
    assert "/workbench/batches" in script
    assert "batch-submit-form" in script
    assert "data-batch-cancel" in script
    assert "data-batch-retry" in script
    assert "data-batch='" in script
    assert '"batch_execution"' in script
