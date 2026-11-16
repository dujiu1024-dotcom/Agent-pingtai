"""Fail-closed validation and extraction for extension ZIP packages."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class ExtensionExtractionReport:
    destination: Path
    files_extracted: int
    uncompressed_bytes: int
    archive_sha256: str


def _safe_member_name(name: str, *, is_directory: bool) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise ValueError("extension archive contains an unsafe path")
    raw_parts = name.split("/")
    if is_directory and raw_parts[-1] == "":
        raw_parts = raw_parts[:-1]
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("extension archive contains an unsafe path")
    if ":" in raw_parts[0]:
        raise ValueError("extension archive contains a drive-qualified path")
    path = PurePosixPath(*raw_parts)
    if path.is_absolute():
        raise ValueError("extension archive contains an absolute path")
    return path


def extract_extension_zip(
    archive: Path,
    destination: Path,
    *,
    max_files: int = 1000,
    max_uncompressed_bytes: int = 64 * 1024 * 1024,
    max_compression_ratio: int = 100,
    expected_archive_sha256: str | None = None,
) -> ExtensionExtractionReport:
    """Validate every ZIP member before writing into a new private directory.

    Symlinks, special files, duplicate/case-colliding paths, traversal, encrypted
    entries, and oversized/high-ratio archives are rejected. Extracted files
    receive owner-only permissions; archive executable bits are not preserved.
    """
    if max_files < 1 or max_uncompressed_bytes < 1 or max_compression_ratio < 1:
        raise ValueError("extension archive limits must be positive")
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("extension archive must be a regular non-symlink file")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("extension extraction destination must not already exist")

    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    parent = parent.resolve()
    destination = parent / destination.name
    temporary_root = Path(tempfile.mkdtemp(prefix=".extension-extract-", dir=parent))
    stage = temporary_root / "package"
    stage.mkdir(mode=0o700)
    seen: set[str] = set()
    file_count = 0
    total_bytes = 0
    try:
        with zipfile.ZipFile(archive, "r") as package:
            members = package.infolist()
            if not members:
                raise ValueError("extension archive is empty")
            for info in members:
                path = _safe_member_name(info.filename, is_directory=info.is_dir())
                normalized = path.as_posix()
                collision_key = normalized.casefold()
                if collision_key in seen:
                    raise ValueError("extension archive contains duplicate or case-colliding paths")
                seen.add(collision_key)
                if info.flag_bits & 0x1:
                    raise ValueError("encrypted extension archive entries are not supported")
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or (kind not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise ValueError("extension archive contains a symlink or special file")
                if info.is_dir():
                    continue
                file_count += 1
                total_bytes += info.file_size
                if file_count > max_files or total_bytes > max_uncompressed_bytes:
                    raise ValueError("extension archive exceeds extraction limits")
                if info.file_size and info.compress_size == 0:
                    raise ValueError("extension archive has an invalid compression size")
                if info.compress_size and info.file_size / info.compress_size > max_compression_ratio:
                    raise ValueError("extension archive compression ratio exceeds limit")

            for info in members:
                path = _safe_member_name(info.filename, is_directory=info.is_dir())
                target = stage.joinpath(*path.parts)
                if not target.resolve().is_relative_to(stage.resolve()):
                    raise ValueError("extension archive path escapes extraction directory")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with package.open(info, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o600)

        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        archive_sha256 = digest.hexdigest()
        if expected_archive_sha256 is not None and archive_sha256 != expected_archive_sha256:
            raise ValueError("extension archive digest does not match verified signature")
        os.replace(stage, destination)
        return ExtensionExtractionReport(
            destination=destination,
            files_extracted=file_count,
            uncompressed_bytes=total_bytes,
            archive_sha256=archive_sha256,
        )
    except Exception:
        raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)



def verify_extension_signature(
    archive: Path,
    signature: bytes,
    trusted_public_key_pem: bytes,
    *,
    openssl_binary: str = "openssl",
    timeout_seconds: float = 10.0,
) -> str:
    """Verify a detached Ed25519 signature over the archive's SHA-256 digest.

    The public key must come from a trusted publisher registry, never from the
    archive itself. OpenSSL is invoked without a shell and with bounded runtime.
    The returned digest should be passed to extract_extension_zip as
    expected_archive_sha256 to bind verification to the extracted bytes.
    """
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("extension archive must be a regular non-symlink file")
    if len(signature) != 64:
        raise ValueError("Ed25519 signature must be exactly 64 bytes")
    if b"-----BEGIN PUBLIC KEY-----" not in trusted_public_key_pem:
        raise ValueError("trusted Ed25519 public key must be PEM encoded")
    if timeout_seconds <= 0:
        raise ValueError("signature verification timeout must be positive")

    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    archive_digest = digest.digest()

    with tempfile.TemporaryDirectory(prefix="extension-signature-") as directory:
        root = Path(directory)
        key_path = root / "trusted-publisher.pem"
        signature_path = root / "package.sig"
        digest_path = root / "archive.sha256"
        key_path.write_bytes(trusted_public_key_pem)
        signature_path.write_bytes(signature)
        digest_path.write_bytes(archive_digest)
        for path in (key_path, signature_path, digest_path):
            path.chmod(0o600)
        try:
            result = subprocess.run(
                [
                    openssl_binary, "pkeyutl", "-verify", "-pubin",
                    "-inkey", str(key_path), "-sigfile", str(signature_path),
                    "-rawin", "-in", str(digest_path),
                ],
                capture_output=True, check=False, timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("OpenSSL Ed25519 signature verification unavailable") from exc
        if result.returncode != 0:
            raise ValueError("extension package signature verification failed")
    return digest.hexdigest()
