"""Signed extension storage and isolated invocation through the OCI sandbox."""
from __future__ import annotations

import base64
import inspect
import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent_platform.extensions.manifest import ExtensionManifest
from agent_platform.extensions.package import verify_extension_signature
from agent_platform.sandbox.oci import SandboxResult


@dataclass(frozen=True)
class SignedExtensionPackage:
    """Untrusted package metadata; it may identify a key but never supply one."""

    archive: bytes
    signature: bytes
    publisher_key_id: str


@dataclass(frozen=True)
class TrustedPublisherKey:
    """A publisher key record returned only by the platform trust registry."""

    publisher_id: str
    key_id: str
    public_key_pem: bytes
    extension_ids: frozenset[str]
    tenant_ids: frozenset[str] | None = None
    not_before: float = 0.0
    expires_at: float | None = None
    revoked: bool = False


class ExtensionPackageResolver(Protocol):
    async def resolve(self, manifest: ExtensionManifest) -> SignedExtensionPackage: ...


class TrustedPublisherKeyResolver(Protocol):
    """Resolve a platform-controlled key binding, not key material from a package."""

    async def resolve(
        self, tenant_id: str, extension_id: str, key_id: str,
    ) -> TrustedPublisherKey | None: ...


class ExtensionArtifactStore(Protocol):
    async def put(self, tenant_id: str, manifest: ExtensionManifest, package: SignedExtensionPackage) -> None: ...
    async def get(self, tenant_id: str, manifest: ExtensionManifest) -> SignedExtensionPackage | None: ...
    async def delete(self, tenant_id: str, manifest: ExtensionManifest) -> None: ...


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
    trusted_key: TrustedPublisherKey,
    *,
    verifier: Callable[[Path, bytes, bytes], str] | None = None,
) -> str:
    if not isinstance(package.archive, bytes) or not package.archive or len(package.archive) > 64 * 1024 * 1024:
        raise ValueError("extension package must be non-empty and at most 64 MiB")
    if not isinstance(package.signature, bytes) or not isinstance(package.publisher_key_id, str) or not package.publisher_key_id:
        raise ValueError("extension package signature and publisher key id are required")
    if not isinstance(trusted_key.public_key_pem, bytes) or b"-----BEGIN PUBLIC KEY-----" not in trusted_key.public_key_pem:
        raise ValueError("trusted publisher registry returned an invalid PEM public key")
    with tempfile.TemporaryDirectory(prefix="extension-verify-") as directory:
        archive = Path(directory) / "package.zip"
        archive.write_bytes(package.archive)
        if verifier is not None:
            return verifier(archive, package.signature, trusted_key.public_key_pem)
        return verify_extension_signature(archive, package.signature, trusted_key.public_key_pem)


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
        if (not name or path.is_absolute() or "\\" in name or
            any(part in ("", ".", "..") for part in name.rstrip("/").split("/")) or
            ":" in name.rstrip("/").split("/")[0] or
            (info.flag_bits & 0x1) or
            (info.file_size and info.compress_size == 0) or
            (info.compress_size and info.file_size / info.compress_size > 100) or
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
    """Run signed extensions in OCI, trusting keys only from the platform registry.

    The package's key id is an untrusted lookup hint. The registry must bind it
    to the extension and tenant and provide the authoritative public key.
    Registry lookup is repeated on invocation so revocation/expiry takes effect
    for already-installed artifacts.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        package_resolver: ExtensionPackageResolver,
        publisher_key_resolver: TrustedPublisherKeyResolver,
        artifact_store: ExtensionArtifactStore,
        sandbox_runner: ExtensionSandboxRunner,
        authorize_permission: Callable[[Any, ExtensionManifest, str], Any],
        audit: Callable[[Any, ExtensionManifest, str, str], Any],
        signature_verifier: Callable[[Path, bytes, bytes], str] | None = None,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not 0 < timeout_seconds <= 120:
            raise ValueError("extension sandbox timeout must be in (0, 120]")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("a tenant-scoped extension runtime is required")
        self.tenant_id = tenant_id
        self.package_resolver = package_resolver
        self.publisher_key_resolver = publisher_key_resolver
        self.artifact_store = artifact_store
        self.sandbox_runner = sandbox_runner
        self.authorize_permission = authorize_permission
        self.audit = audit
        self.signature_verifier = signature_verifier
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    async def _trusted_key(self, manifest: ExtensionManifest, package: SignedExtensionPackage) -> TrustedPublisherKey:
        key = await self.publisher_key_resolver.resolve(
            self.tenant_id, manifest.extension_id, package.publisher_key_id,
        )
        if key is None:
            raise PermissionError("extension publisher key is not trusted")
        now = self.clock()
        if (
            key.key_id != package.publisher_key_id
            or not key.publisher_id
            or manifest.extension_id not in key.extension_ids
            or (key.tenant_ids is not None and self.tenant_id not in key.tenant_ids)
            or key.revoked
            or now < key.not_before
            or (key.expires_at is not None and now >= key.expires_at)
        ):
            raise PermissionError("extension publisher key is not valid for this extension and tenant")
        return key

    async def install(self, manifest: ExtensionManifest) -> None:
        package = await self.package_resolver.resolve(manifest)
        trusted_key = await self._trusted_key(manifest, package)
        _verify_signed_package(package, trusted_key, verifier=self.signature_verifier)
        await self.artifact_store.put(self.tenant_id, manifest, package)

    async def uninstall(self, manifest: ExtensionManifest) -> None:
        await self.artifact_store.delete(self.tenant_id, manifest)

    async def invoke(
        self,
        manifest: ExtensionManifest,
        *,
        principal: Any,
        action: str,
        payload: Mapping[str, Any],
    ) -> Any:
        if getattr(principal, "tenant_id", None) != self.tenant_id:
            raise PermissionError("extension invocation tenant mismatch")
        if not isinstance(action, str) or not action or len(action) > 128:
            raise ValueError("extension action must be non-empty and at most 128 characters")
        if not isinstance(payload, Mapping):
            raise TypeError("extension payload must be a mapping")
        for permission in manifest.permissions:
            allowed = await _call(self.authorize_permission, principal, manifest, permission)
            if allowed is not True:
                raise PermissionError("extension permission denied")

        package = await self.artifact_store.get(self.tenant_id, manifest)
        if package is None:
            raise LookupError("extension package is not installed")
        trusted_key = await self._trusted_key(manifest, package)
        _verify_signed_package(package, trusted_key, verifier=self.signature_verifier)
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
