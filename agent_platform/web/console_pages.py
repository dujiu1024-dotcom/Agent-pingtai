"""三个独立看板页面：运维状态、任务全局、指令中心。

页面为自包含静态资源，仅读取平台既有接口，不修改现有前端页面。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

CONSOLE_DIR = Path(__file__).resolve().parent / "console"

PAGES = {
    "ops-console": "ops.html",
    "task-board": "tasks.html",
    "command-center": "command.html",
}

ASSETS = {
    "console.css": "text/css; charset=utf-8",
    "common.js": "application/javascript; charset=utf-8",
    "ops.js": "application/javascript; charset=utf-8",
    "tasks.js": "application/javascript; charset=utf-8",
    "command.js": "application/javascript; charset=utf-8",
}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
    }


def create_console_pages_router() -> APIRouter:
    router = APIRouter(tags=["platform-console"], include_in_schema=False)

    @router.get("/console")
    async def console_root() -> RedirectResponse:
        return RedirectResponse(url="/ops-console", status_code=307)

    @router.get("/ops-console")
    @router.get("/ops-console/")
    async def ops_console() -> FileResponse:
        return FileResponse(
            CONSOLE_DIR / PAGES["ops-console"],
            media_type="text/html; charset=utf-8",
            headers=_headers(),
        )

    @router.get("/task-board")
    @router.get("/task-board/")
    async def task_board() -> FileResponse:
        return FileResponse(
            CONSOLE_DIR / PAGES["task-board"],
            media_type="text/html; charset=utf-8",
            headers=_headers(),
        )

    @router.get("/command-center")
    @router.get("/command-center/")
    async def command_center() -> FileResponse:
        return FileResponse(
            CONSOLE_DIR / PAGES["command-center"],
            media_type="text/html; charset=utf-8",
            headers=_headers(),
        )

    @router.get("/console-asset/{asset_name}")
    async def console_asset(asset_name: str) -> FileResponse:
        media_type = ASSETS.get(asset_name)
        if media_type is None:
            raise HTTPException(status_code=404, detail="asset not found")
        path = (CONSOLE_DIR / asset_name).resolve()
        if not path.is_file() or path.parent != CONSOLE_DIR.resolve():
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(path, media_type=media_type, headers=_headers())

    return router
