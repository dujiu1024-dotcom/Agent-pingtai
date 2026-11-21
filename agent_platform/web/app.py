import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

STATIC = Path(__file__).with_name("static")


def _index_response() -> FileResponse:
    if not (STATIC / "index.html").is_file():
        raise HTTPException(503, "frontend build is missing")
    return FileResponse(
        STATIC / "index.html",
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def create_platform_web_router() -> APIRouter:
    router = APIRouter(tags=["platform-web"])

    @router.get("/app", include_in_schema=False)
    @router.get("/app/", include_in_schema=False)
    async def index():
        return _index_response()

    @router.get("/app/assets/{path:path}", include_in_schema=False)
    async def asset(path: str):
        """通配符 assets — 支持 Vite hash 产物（如 index-CQC_7KwS.js）。"""
        f = STATIC / "assets" / path
        if not f.is_file():
            raise HTTPException(404, "asset not found")
        media_type, _ = mimetypes.guess_type(str(f))
        return FileResponse(f, media_type=media_type or "application/octet-stream")

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
        """React Router SPA fallback — 任意未匹配子路由返回 index.html。"""
        return _index_response()

    return router
