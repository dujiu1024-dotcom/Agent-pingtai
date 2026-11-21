"""本地文件密钥库。

密钥只落在仓库之外的私有目录（默认 ``~/.fde-local-secrets``），目录权限
700、文件权限 600，按租户分子目录隔离。引用格式与平台 SecretManager 一致：
``secret://<tenant>/<kind>/<id>``。这里只保存密文材料本身，不落任何元数据表。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

_SECRET_REF = re.compile(r"^secret://([a-zA-Z0-9_-]{1,64})/([a-zA-Z0-9._/-]{1,240})$")
_KIND = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


class LocalSecretStore:
    """满足 Webhook / 连接器所需的本地密钥读写接口。"""

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.environ.get("LOCAL_SECRET_DIR") or "~/.fde-local-secrets"
        self.root = Path(configured).expanduser()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    # ---------- 引用与路径 ----------
    @staticmethod
    def _parts(secret_ref: str) -> tuple[str, str]:
        match = _SECRET_REF.fullmatch(secret_ref)
        if not match:
            raise ValueError("secret_ref must be a scoped secret:// reference")
        tenant, rest = match.group(1), match.group(2)
        if ".." in rest or rest.startswith("/") or "//" in rest or rest.endswith("/"):
            raise ValueError("secret_ref contains an invalid path")
        return tenant, rest

    def _path(self, secret_ref: str) -> Path:
        tenant, rest = self._parts(secret_ref)
        path = self.root / tenant / rest
        root = self.root.resolve()
        if root not in path.resolve().parents and path.resolve() != root:
            raise ValueError("secret_ref escapes the local secret root")
        return path

    # ---------- 写入 ----------
    def _secure_dirs(self, path: Path) -> None:
        """从密钥目录逐级向上收紧权限，避免中间目录因 umask 变成 755。"""
        root = self.root.resolve()
        current = path.resolve()
        while current.is_relative_to(root):
            try:
                os.chmod(current, 0o700)
            except OSError:
                pass
            if current == root:
                break
            current = current.parent

    def _write(self, secret_ref: str, value: bytes) -> None:
        path = self._path(secret_ref)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._secure_dirs(path.parent)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as handle:
            handle.write(value)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    async def store(self, *, tenant_id: str, secret: bytes | str, kind: str = "webhook") -> str:
        """写入一段密钥并返回引用；Webhook 服务按关键字参数调用。"""
        tenant = (tenant_id or "").strip()
        if not tenant or not _KIND.fullmatch(kind):
            raise ValueError("tenant_id and kind are required")
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if not raw:
            raise ValueError("secret value must not be empty")
        secret_ref = f"secret://{tenant}/{kind}/{uuid4().hex}"
        self._write(secret_ref, raw)
        return secret_ref

    async def store_json(self, *, tenant_id: str, payload: Mapping[str, Any],
                         kind: str = "connector") -> str:
        value = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        return await self.store(tenant_id=tenant_id, secret=value, kind=kind)

    # ---------- 读取 ----------
    def get_bytes(self, secret_ref: str) -> bytes | None:
        path = self._path(secret_ref)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    async def resolve_bytes(self, secret_ref: str) -> bytes:
        value = self.get_bytes(secret_ref)
        if value is None:
            raise KeyError("secret material is unavailable")
        return value

    async def resolve(self, secret_ref: str) -> str:
        return (await self.resolve_bytes(secret_ref)).decode("utf-8")

    # ---------- 撤销 ----------
    async def revoke(self, *, secret_ref: str) -> None:
        path = self._path(secret_ref)
        try:
            path.unlink()
        except FileNotFoundError:
            return
