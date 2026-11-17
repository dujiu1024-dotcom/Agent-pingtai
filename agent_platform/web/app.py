from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

STATIC = Path(__file__).with_name("static")
ASSETS = {"app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}

def create_platform_web_router() -> APIRouter:
    router = APIRouter(tags=["platform-web"])
    @router.get("/app", include_in_schema=False)
    @router.get("/app/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")
    @router.get("/app/sw.js", include_in_schema=False)
    async def service_worker():
        return FileResponse(
            STATIC / "sw.js",
            media_type="text/javascript; charset=utf-8",
            headers={"Service-Worker-Allowed": "/app/"},
        )
    @router.get("/app/assets/{name}", include_in_schema=False)
    async def asset(name: str):
        if name not in ASSETS or not (STATIC / name).is_file():
            raise HTTPException(404, "asset not found")
        return FileResponse(STATIC / name, media_type=ASSETS[name])
    return router
