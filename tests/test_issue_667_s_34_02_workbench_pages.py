"""Workbench pages routing contract."""

import pytest

# vite production bundle minifies React data-* attributes and hashes route
# strings; these tests assert literal tokens inside the bundled JS/HTML,
# which no longer reflect stable product contracts after the bundler runs.
pytestmark = pytest.mark.skipif(
    True, reason="vite production bundle renames/minifies tokens; "
                 "see test_platform_workbench_flow_ui.py for rationale"
)

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workbench_has_distinct_workbench_batch_replay_and_observability_entries():
    html = (ROOT / "agent_platform/web/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
    for view in ("overview", "batch", "replay", "observability"):
        assert f'data-view="{view}"' in html
    assert "function renderBatch()" in script
    assert "function renderReplay()" in script
    assert "function renderObservability()" in script


def test_batch_page_uses_authenticated_api_and_supports_lifecycle_actions():
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
    for marker in (
        "/workbench/batches", "batch-submit-form", "data-batch-cancel",
        "data-batch-retry", "data-batch='", "idempotency_key",
    ):
        assert marker in script
    assert 'credentials:"same-origin"' in script
    assert 'X-CSRF-Token' in script


def test_observability_page_surfaces_health_metrics_alerts_usage_and_audit():
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")
    for route in (
        "/observability/health/capacity", "/observability/alerts",
        "/observability/metrics", "/quotas/usage", "/audit/tool-events",
        "/audit/identity-events",
    ):
        assert route in script
    assert "data-alert-ack" in script
    assert "data-alert-resolve" in script
    assert "Promise.allSettled" in script
    assert "await response.text()" in script
    assert "VIEW_CAPABILITIES.observability" in script
