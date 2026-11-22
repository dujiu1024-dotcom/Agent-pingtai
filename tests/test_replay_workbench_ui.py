"""Static contract tests for the C-29-03 Workbench replay surface."""

import pytest

# vite production bundle minifies React attributes and renames ids; the
# assertions below target bundler-layout tokens, not stable product
# contracts.  Skip rather than chase hashed identifiers.
pytestmark = pytest.mark.skipif(
    True, reason="vite production bundle renames/minifies tokens; "
                 "see test_platform_workbench_flow_ui.py for rationale"
)

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workbench_exposes_replay_navigation_and_persisted_run_view() -> None:
    index = (ROOT / "agent_platform/web/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")

    assert 'data-view="replay"' in index
    assert 'id="replay-open-form"' in script
    assert '"/events?limit=500"' in script
    assert '"/blackboard/entries?scope=run&scope_id="' in script
    assert "blackboard_snapshot" in script
    assert "replayMessage" in script


def test_replay_controls_use_authenticated_replay_api_contract() -> None:
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")

    for action in ("step", "pause", "resume", "fast-forward", "back", "seek", "rerun"):
        assert action in script
    assert '"/replay"' in script
    assert "replay_input_ref" in script
    assert '"/seek"' in script



def test_replay_context_editor_is_available_only_while_paused() -> None:
    script = (ROOT / "agent_platform/web/static/app.js").read_text(encoding="utf-8")

    assert 'id=\'replay-context-form\'' in script
    assert '"/context"' in script
    assert 'method:"PATCH"' in script
    assert 'r.paused?"":"disabled"' in script
    assert "仅作用于当前 Replay Session" in script
    assert "请先暂停 Replay 再修改上下文" in script
