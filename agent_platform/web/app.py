from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

STATIC = Path(__file__).with_name("static")
LEGACY_CONSOLE = Path(__file__).resolve().parents[2] / "console.html"
ASSETS = {"app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}


def _legacy_console_response() -> FileResponse:
    if not LEGACY_CONSOLE.is_file():
        raise HTTPException(503, "legacy console fallback is unavailable")
    return FileResponse(
        LEGACY_CONSOLE,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def create_platform_web_router() -> APIRouter:
    router = APIRouter(tags=["platform-web"])

    @router.get("/app", include_in_schema=False)
    @router.get("/app/", include_in_schema=False)
    async def index(legacy: bool = False):
        # Explicit opt-in keeps the legacy UI available during an incomplete
        # deployment. Missing new UI files also fail over without a dead link.
        if legacy or not (STATIC / "index.html").is_file():
            return _legacy_console_response()
        return FileResponse(
            STATIC / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/app/assets/{name}", include_in_schema=False)
    async def asset(name: str):
        if name not in ASSETS or not (STATIC / name).is_file():
            raise HTTPException(404, "asset not found")
        return FileResponse(STATIC / name, media_type=ASSETS[name])

    return router
