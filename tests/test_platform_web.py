from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_platform.web.app import create_platform_web_router

def test_platform_web_serves_independent_assets():
    app = FastAPI()
    app.include_router(create_platform_web_router())
    client = TestClient(app)
    page = client.get("/app/")
    assert page.status_code == 200
    assert "Agent Platform" in page.text
    assert "console.py" not in page.text
    assert client.get("/app/assets/app.js").status_code == 200
    assert "workbench/capabilities" in client.get("/app/assets/app.js").text
    assert client.get("/app/assets/style.css").status_code == 200

def test_platform_web_rejects_unknown_asset():
    app = FastAPI()
    app.include_router(create_platform_web_router())
    assert TestClient(app).get("/app/assets/not-allowed.js").status_code == 404


def test_platform_web_exposes_responsive_accessible_workbench_and_shared_api_transport():
    app = FastAPI()
    app.include_router(create_platform_web_router())
    client = TestClient(app)

    page = client.get("/app/")
    assert 'name="viewport"' in page.text
    assert 'aria-label="平台工作台导航"' in page.text
    assert 'aria-current="page"' in page.text

    styles = client.get("/app/assets/style.css").text
    assert "@media(max-width:640px)" in styles
    assert "@media(max-width:380px)" in styles
    assert "focus-visible" in styles
    assert "prefers-reduced-motion:reduce" in styles
    assert "scroll-snap-type:x proximity" in styles

    script = client.get("/app/assets/app.js").text
    assert "async function request(path,opt={})" in script
    assert 'credentials:"same-origin"' in script
    assert 'h["X-CSRF-Token"]=csrf()' in script
    assert 'request(API+"/files/"+encodeURIComponent(b.dataset.download))' in script
    assert "workbench/capabilities" in script
    assert "Capability visibility is presentation-only" in script
    assert "async function upload(path,file){const r=await request(path" in script
    assert 'b.setAttribute("aria-current","page")' in script
