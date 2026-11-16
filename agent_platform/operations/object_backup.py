"""Streaming, checksummed object backup primitives with path traversal defenses."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


@dataclass(frozen=True)
class BackupEntry:
    key: str
    size_bytes: int
    sha256: str


def _safe_key(key: str) -> PurePosixPath:
    if not isinstance(key, str) or not key or "\\" in key or "\x00" in key:
        raise ValueError("invalid object key")
    path = PurePosixPath(key)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("unsafe object key")
    return path


def stream_object_to_file(source: BinaryIO, destination: Path, *, key: str) -> BackupEntry:
    """Copy in bounded chunks, fsync, and atomically publish one object."""
    safe = _safe_key(key)
    destination = destination.resolve()
    target = destination.joinpath(*safe.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise FileExistsError("backup destination already exists")
    digest = hashlib.sha256()
    size = 0
    fd, temporary = tempfile.mkstemp(prefix=".backup-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise TypeError("object stream must return bytes")
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return BackupEntry(key=key, size_bytes=size, sha256=digest.hexdigest())


def write_backup_manifest(path: Path, entries: Iterable[BackupEntry]) -> None:
    ordered = sorted(entries, key=lambda item: item.key)
    keys = [item.key for item in ordered]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate backup object key")
    payload = {"version": 1, "objects": [asdict(item) for item in ordered]}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)
