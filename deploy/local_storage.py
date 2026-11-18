"""本地文件系统对象存储适配器（仅用于本机部署）。

遵循 ObjectStorage 协议：对象键必须是 tenant/ 前缀的安全 POSIX 路径，
内容写入为原子替换，避免半写文件被读取。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath

from agent_platform.control_plane.sessions_assets import SessionNotFound


class LocalFileObjectStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_key(object_key: str) -> str:
        path = PurePosixPath(object_key)
        if (
            not object_key.startswith("tenant/")
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in object_key.split("/"))
            or "\\" in object_key
        ):
            raise ValueError("object key must be a safe tenant-scoped POSIX key")
        return str(path)

    def _path(self, object_key: str) -> Path:
        key = self._validate_key(object_key)
        target = (self._root / key).resolve()
        if self._root not in target.parents:
            raise ValueError("object key escapes the storage root")
        return target

    async def put(self, object_key: str, content: bytes, *, media_type: str) -> None:
        if not content:
            raise ValueError("empty object payload is not allowed")
        target = self._path(object_key)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, target)

        await asyncio.to_thread(write)

    async def get(self, object_key: str) -> bytes:
        target = self._path(object_key)
        try:
            return await asyncio.to_thread(target.read_bytes)
        except FileNotFoundError as exc:
            raise SessionNotFound("object not found") from exc

    async def delete(self, object_key: str) -> None:
        target = self._path(object_key)
        await asyncio.to_thread(lambda: target.unlink(missing_ok=True))
