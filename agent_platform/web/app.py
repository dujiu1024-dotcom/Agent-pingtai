import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

STATIC = Path(__file__).with_name("static")


def _index_response() -> HTMLResponse:
    index = STATIC / "index.html"
    if not index.is_file():
        raise HTTPException(503, "platform UI build is not installed")
    return HTMLResponse(
        index.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


def create_platform_web_router() -> APIRouter:
    router = APIRouter(tags=["platform-web"])

    @router.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/app/", status_code=302)

    @router.get("/app", include_in_schema=False)
    @router.get("/app/", include_in_schema=False)
    async def index():
        return _index_response()

    @router.get("/app/sw.js", include_in_schema=False)
    async def service_worker():
        """Serve the offline-fallback service worker."""
        sw_path = (STATIC / "sw.js").resolve()
        if not sw_path.is_file() or not sw_path.is_relative_to(STATIC.resolve()):
            raise HTTPException(404, "service worker not installed")
        return FileResponse(
            sw_path,
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-cache",
                "Service-Worker-Allowed": "/app/",
            },
        )

    @router.get("/app/assets/{path:path}", include_in_schema=False)
    async def asset(path: str):
        """Serve built UI assets without allowing traversal outside static/assets."""
        asset_root = (STATIC / "assets").resolve()
        file_path = (asset_root / path).resolve()
        if not file_path.is_relative_to(asset_root) or not file_path.is_file():
            raise HTTPException(404, "asset not found")
        media_type, _ = mimetypes.guess_type(str(file_path))
        return FileResponse(
            file_path,
            media_type=media_type or "application/octet-stream",
        )

    @router.get("/graph-view", include_in_schema=False)
    async def graph_view():
        if not (STATIC / "graph.html").is_file():
            raise HTTPException(404, "graph view is not installed")
        return FileResponse(
            STATIC / "graph.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/app/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        """React Router SPA fallback."""
        return _index_response()

    return router
