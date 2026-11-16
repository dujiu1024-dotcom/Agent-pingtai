"""Signed extension storage and isolated invocation through the OCI sandbox."""
from __future__ import annotations

import base64
import inspect
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent_platform.extensions.manifest import ExtensionManifest
from agent_platform.extensions.package import verify_extension_signature
from agent_platform.sandbox.oci import SandboxResult


@dataclass(frozen=True)
class SignedExtensionPackage:
    archive: bytes
    signature: bytes
    trusted_public_key_pem: bytes


class ExtensionPackageResolver(Protocol):
    async def resolve(self, manifest: ExtensionManifest) -> SignedExtensionPackage: ...


class ExtensionArtifactStore(Protocol):
    async def put(self, manifest: ExtensionManifest, package: SignedExtensionPackage) -> None: ...
    async def get(self, manifest: ExtensionManifest) -> SignedExtensionPackage | None: ...
    async def delete(self, manifest: ExtensionManifest) -> None: ...


class ExtensionSandboxRunner(Protocol):
    async def run(
        self,
        command: Sequence[str],
        *,
        stdin: bytes = b"",
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> SandboxResult: ...


async def _call(callback: Callable[..., Any], *args: Any) -> Any:
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


def _verify_signed_package(
    package: SignedExtensionPackage,
    *,
    verifier: Callable[[Path, bytes, bytes], str] | None = None,
) -> str:
    if not isinstance(package.archive, bytes) or not package.archive or len(package.archive) > 64 * 1024 * 1024:
        raise ValueError("extension package must be non-empty and at most 64 MiB")
    verify = verifier
    with tempfile.TemporaryDirectory(prefix="extension-verify-") as directory:
        archive = Path(directory) / "package.zip"
        archive.write_bytes(package.archive)
        if verify is not None:
            return verify(archive, package.signature, package.trusted_public_key_pem)
        return verify_extension_signature(
            archive, package.signature, package.trusted_public_key_pem,
        )


_BOOTSTRAP = r"""
import base64, io, json, pathlib, runpy, stat, sys, tempfile, zipfile
request = json.loads(sys.stdin.buffer.read())
archive = base64.b64decode(request["archive"], validate=True)
entrypoint = request["entrypoint"]
root = pathlib.Path(tempfile.mkdtemp(prefix="extension-"))
seen = set()
total = 0
with zipfile.ZipFile(io.BytesIO(archive), "r") as package:
    members = package.infolist()
    if not members or len(members) > 1000:
        raise RuntimeError("invalid extension archive member count")
    for info in members:
        name = info.filename
        path = pathlib.PurePosixPath(name)
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if (not name or path.is_absolute() or "\" in name or
            any(part in ("", ".", "..") for part in name.rstrip("/").split("/")) or
            (kind not in (0, stat.S_IFREG, stat.S_IFDIR)) or stat.S_ISLNK(mode)):
            raise RuntimeError("unsafe extension archive member")
        key = path.as_posix().casefold()
        if key in seen:
            raise RuntimeError("duplicate extension archive member")
        seen.add(key)
        if not info.is_dir():
            total += info.file_size
            if total > 64 * 1024 * 1024:
                raise RuntimeError("extension archive too large")
    for info in members:
        target = root.joinpath(*pathlib.PurePosixPath(info.filename).parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.read(info))
target = root.joinpath(*pathlib.PurePosixPath(entrypoint).parts)
if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
    raise RuntimeError("extension entrypoint missing")
namespace = runpy.run_path(str(target), run_name="extension_entry")
handler = namespace.get("handle")
if not callable(handler):
    raise RuntimeError("extension entrypoint must define handle(action, payload)")
result = handler(request["action"], request["payload"])
sys.stdout.write(json.dumps({"result": result}, ensure_ascii=False, separators=(",", ":")))
"""


class SandboxedExtensionRuntime:
    """Extension lifecycle adapter; untrusted entrypoint code runs only in OCI.

    Install verifies the detached signature against a trusted publisher key and
    stores the immutable package. Invocation re-verifies it, checks every
    declared permission against the caller, and starts a network-isolated OCI
    sandbox. No host paths, credentials, or Docker socket are mounted.
    """

    def __init__(
        self,
        *,
        package_resolver: ExtensionPackageResolver,
        artifact_store: ExtensionArtifactStore,
        sandbox_runner: ExtensionSandboxRunner,
        authorize_permission: Callable[[Any, ExtensionManifest, str], Any],
        audit: Callable[[Any, ExtensionManifest, str, str], Any],
        signature_verifier: Callable[[Path, bytes, bytes], str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not 0 < timeout_seconds <= 120:
            raise ValueError("extension sandbox timeout must be in (0, 120]")
        self.package_resolver = package_resolver
        self.artifact_store = artifact_store
        self.sandbox_runner = sandbox_runner
        self.authorize_permission = authorize_permission
        self.audit = audit
        self.signature_verifier = signature_verifier
        self.timeout_seconds = timeout_seconds

    async def install(self, manifest: ExtensionManifest) -> None:
        package = await self.package_resolver.resolve(manifest)
        _verify_signed_package(package, verifier=self.signature_verifier)
        await self.artifact_store.put(manifest, package)

    async def uninstall(self, manifest: ExtensionManifest) -> None:
        await self.artifact_store.delete(manifest)

    async def invoke(
        self,
        manifest: ExtensionManifest,
        *,
        principal: Any,
        action: str,
        payload: Mapping[str, Any],
    ) -> Any:
        if not isinstance(action, str) or not action or len(action) > 128:
            raise ValueError("extension action must be non-empty and at most 128 characters")
        if not isinstance(payload, Mapping):
            raise TypeError("extension payload must be a mapping")
        for permission in manifest.permissions:
            allowed = await _call(
                self.authorize_permission, principal, manifest, permission,
            )
            if allowed is not True:
                raise PermissionError("extension permission denied")

        package = await self.artifact_store.get(manifest)
        if package is None:
            raise LookupError("extension package is not installed")
        _verify_signed_package(package, verifier=self.signature_verifier)
        await _call(self.audit, principal, manifest, action, "requested")

        request = json.dumps(
            {
                "archive": base64.b64encode(package.archive).decode("ascii"),
                "entrypoint": manifest.entrypoint,
                "action": action,
                "payload": dict(payload),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        result = await self.sandbox_runner.run(
            ("python", "-I", "-c", _BOOTSTRAP),
            stdin=request,
            timeout_seconds=self.timeout_seconds,
        )
        if result.timed_out:
            await _call(self.audit, principal, manifest, action, "timed_out")
            raise TimeoutError("extension sandbox execution timed out")
        if result.returncode != 0:
            await _call(self.audit, principal, manifest, action, "failed")
            raise RuntimeError("extension sandbox execution failed")
        if result.output_truncated:
            await _call(self.audit, principal, manifest, action, "output_truncated")
            raise RuntimeError("extension sandbox output exceeded its configured limit")
        try:
            response = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            await _call(self.audit, principal, manifest, action, "invalid_output")
            raise RuntimeError("extension sandbox returned invalid JSON") from exc
        await _call(self.audit, principal, manifest, action, "succeeded")
        return response.get("result")
