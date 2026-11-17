"""Responsive and accessible layout contract for the platform workbench (C-14-05)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.web.app import create_platform_web_router


def _assets() -> tuple[str, str]:
    app = FastAPI()
    app.include_router(create_platform_web_router())
    client = TestClient(app)
    page = client.get("/app/")
    assert page.status_code == 200
    css = client.get("/app/assets/style.css")
    script = client.get("/app/assets/app.js")
    assert css.status_code == 200
    assert script.status_code == 200
    return css.text, script.text


def test_workbench_has_desktop_tablet_and_mobile_layout_breakpoints():
    css, _ = _assets()

    assert "grid-template-columns:var(--nav-width) minmax(0,1fr)" in css
    assert "--nav-width:230px" in css
    assert "@media(max-width:900px)" in css
    assert "--nav-width:190px" in css
    assert "@media(max-width:640px)" in css
    assert "body{display:flex;flex-direction:column;min-height:100dvh}" in css
    assert "@media(max-width:380px)" in css
    assert "html{min-width:320px" in css


def test_mobile_navigation_and_data_tables_remain_operable_without_page_overflow():
    css, _ = _assets()

    assert "overflow-x:auto" in css
    assert "scroll-snap-type:x proximity" in css
    assert "nav button{display:inline-flex" in css
    assert "min-height:44px" in css
    assert ".table-scroll{max-width:100%" in css
    assert "table{min-width:620px" in css
    assert "overscroll-behavior-x:contain" in css
    assert "min-width:0" in css


def test_workbench_supports_keyboard_focus_reduced_motion_and_mobile_safe_areas():
    css, _ = _assets()

    assert ":focus-visible" in css
    assert "outline:3px solid" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert "env(safe-area-inset-top)" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "env(safe-area-inset-right)" in css


def test_navigation_updates_current_page_semantics_when_switching_views():
    _, script = _assets()

    assert 'b.setAttribute("aria-current","page")' in script
    assert 'b.removeAttribute("aria-current")' in script
    assert 'b.classList.toggle("active",active)' in script
