import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

STATIC = Path(__file__).with_name("static")
LEGACY_CONSOLE = Path(__file__).resolve().parents[2] / "console.html"


def _legacy_console_response() -> FileResponse:
    if not LEGACY_CONSOLE.is_file():
        raise HTTPException(503, "legacy console fallback is unavailable")
    return FileResponse(
        LEGACY_CONSOLE,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _index_response() -> FileResponse:
    if not (STATIC / "index.html").is_file():
        return _legacy_console_response()
    return FileResponse(
        STATIC / "index.html",
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def create_platform_web_router() -> APIRouter:
    router = APIRouter(tags=["platform-web"])

    @router.get("/app", include_in_schema=False)
    @router.get("/app/", include_in_schema=False)
    async def index(legacy: bool = False):
        # The new platform UI is the default. Legacy mode is explicit and
        # also serves as a fail-safe when the new UI build is absent.
        if legacy or not (STATIC / "index.html").is_file():
            return _legacy_console_response()
        return _index_response()

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
        """React Router SPA fallback; missing builds use the legacy console."""
        return _index_response()

    return router
