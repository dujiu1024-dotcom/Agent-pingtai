"""Tenant-scoped Skill bundle loading through the shared OCI sandbox.

Agent Skills are declarative instruction bundles, not executable plugins. This
adapter scans and validates each archive, stores it per tenant, and performs
bounded, isolated extraction/rehydration in a fresh OCI container on load.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from agent_platform.control_plane.skill_manifest import (
    SkillManifest, parse_skill_document, validate_skill_bundle_paths,
)
from agent_platform.sandbox.oci import SandboxResult


class SkillSandboxError(RuntimeError):
    """A Skill bundle could not be safely installed or loaded."""


class SkillBundleScanner(Protocol):
    async def scan(self, filename: str, media_type: str, content: bytes) -> bool: ...


class SkillSandboxRunner(Protocol):
    async def run(
        self, command: Sequence[str], *, stdin: bytes = b"",
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> SandboxResult: ...


class SkillArtifactStore(Protocol):
    async def put(self, tenant_id: str, skill_id: str, version: int, archive: bytes) -> None: ...
    async def get(self, tenant_id: str, skill_id: str, version: int) -> bytes | None: ...
    async def delete(self, tenant_id: str, skill_id: str, version: int) -> None: ...


class InMemorySkillArtifactStore:
    """Test adapter; production should provide tenant-bound durable storage."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, int], bytes] = {}

    async def put(self, tenant_id: str, skill_id: str, version: int, archive: bytes) -> None:
        self._items[(tenant_id, skill_id, version)] = bytes(archive)

    async def get(self, tenant_id: str, skill_id: str, version: int) -> bytes | None:
        value = self._items.get((tenant_id, skill_id, version))
        return bytes(value) if value is not None else None

    async def delete(self, tenant_id: str, skill_id: str, version: int) -> None:
        self._items.pop((tenant_id, skill_id, version), None)


@dataclass(frozen=True)
class LoadedSkill:
    tenant_id: str
    skill_id: str
    version: int
    manifest: SkillManifest
    instructions: str
    bundle_paths: tuple[str, ...]


# The container only extracts validated instruction data and emits it as JSON.
# It never imports or evaluates files from the bundle.
_BOOTSTRAP = r"""
import base64, io, json, pathlib, stat, sys, zipfile
request = json.loads(sys.stdin.buffer.read())
archive = base64.b64decode(request["archive"], validate=True)
expected = set(request["bundle_paths"])
root = pathlib.Path("/workspace/skill")
root.mkdir(parents=True, exist_ok=True)
seen = set()
total = 0
with zipfile.ZipFile(io.BytesIO(archive), "r") as bundle:
    members = bundle.infolist()
    if not members or len(members) > 256:
        raise RuntimeError("invalid Skill bundle member count")
    for info in members:
        name = info.filename
        path = pathlib.PurePosixPath(name)
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if (not name or path.is_absolute() or "\\" in name or
            any(part in ("", ".", "..") for part in name.rstrip("/").split("/")) or
            ":" in name.rstrip("/").split("/")[0] or
            info.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR) or
            stat.S_ISLNK(mode)):
            raise RuntimeError("unsafe Skill bundle member")
        key = path.as_posix().casefold()
        if key in seen:
            raise RuntimeError("duplicate Skill bundle path")
        seen.add(key)
        if not info.is_dir():
            total += info.file_size
            if total > 8 * 1024 * 1024:
                raise RuntimeError("Skill bundle too large")
    if seen != expected:
        raise RuntimeError("Skill bundle changed after validation")
    for info in members:
        target = root.joinpath(*pathlib.PurePosixPath(info.filename).parts)
        if not target.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Skill bundle path escaped sandbox")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(info))
document = (root / "SKILL.md").read_text(encoding="utf-8")
sys.stdout.write(json.dumps({"skill_md": document}, ensure_ascii=False, separators=(",", ":")))
"""


class SkillSandboxRuntime:
    """Install, load and uninstall declarative Skills using the OCI runner.

    Scanner failures, infected packages, tenant mismatches and malformed
    bundles fail closed. Every load starts a fresh container via OciSandboxRunner.
    """

    def __init__(
        self, *, tenant_id: str, artifact_store: SkillArtifactStore,
        scanner: SkillBundleScanner, sandbox_runner: SkillSandboxRunner,
        available_tools: set[str], audit: Any = None, timeout_seconds: float = 15.0,
        max_bundle_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant-scoped Skill runtime is required")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Skill sandbox timeout must be in (0, 60]")
        if not 1 <= max_bundle_bytes <= 8 * 1024 * 1024:
            raise ValueError("Skill bundle limit must be between 1 byte and 8 MiB")
        self.tenant_id = tenant_id
        self.artifact_store = artifact_store
        self.scanner = scanner
        self.sandbox_runner = sandbox_runner
        self.available_tools = set(available_tools)
        self.audit = audit
        self.timeout_seconds = timeout_seconds
        self.max_bundle_bytes = max_bundle_bytes

    async def _audit(self, actor: Any, skill_id: str, action: str, status: str) -> None:
        if self.audit is None:
            return
        value = self.audit(actor, skill_id, action, status)
        if hasattr(value, "__await__"):
            await value

    @staticmethod
    def _inspect_archive(archive: bytes) -> tuple[str, ...]:
        import io
        import zipfile
        import stat
        if not isinstance(archive, bytes) or not archive:
            raise SkillSandboxError("Skill bundle must be non-empty bytes")
        try:
            with zipfile.ZipFile(io.BytesIO(archive), "r") as bundle:
                paths = []
                for info in bundle.infolist():
                    mode = info.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if stat.S_ISLNK(mode) or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise SkillSandboxError("Skill bundle contains a link or special file")
                    if not info.is_dir():
                        paths.append(info.filename)
                if len(paths) > 256:
                    raise SkillSandboxError("Skill bundle has too many files")
                return validate_skill_bundle_paths(tuple(paths))
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            if isinstance(exc, SkillSandboxError):
                raise
            raise SkillSandboxError("Skill bundle archive is invalid") from exc

    async def install(
        self, *, skill_id: str, version: int, archive: bytes,
        actor: Any, tenant_id: str | None = None,
    ) -> SkillManifest:
        if tenant_id is not None and tenant_id != self.tenant_id:
            raise PermissionError("Skill installation tenant mismatch")
        if not isinstance(skill_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_id):
            raise ValueError("invalid Skill id")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("Skill version must be a positive integer")
        if not isinstance(archive, bytes) or not archive or len(archive) > self.max_bundle_bytes:
            raise SkillSandboxError("Skill bundle is empty or exceeds the configured size limit")
        paths = self._inspect_archive(archive)
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(archive), "r") as bundle:
            try:
                document = bundle.read("SKILL.md").decode("utf-8")
            except (KeyError, UnicodeDecodeError) as exc:
                raise SkillSandboxError("Skill bundle must contain UTF-8 SKILL.md") from exc
        manifest = parse_skill_document(document, available_tools=self.available_tools)
        if manifest.name != skill_id:
            raise SkillSandboxError("Skill id does not match SKILL.md manifest")
        await self._audit(actor, skill_id, "install", "scanning")
        try:
            clean = await self.scanner.scan(skill_id + ".zip", "application/zip", archive)
        except Exception as exc:
            await self._audit(actor, skill_id, "install", "scan_error")
            raise SkillSandboxError("Skill security scan failed; installation denied") from exc
        if clean is not True:
            await self._audit(actor, skill_id, "install", "infected")
            raise SkillSandboxError("Skill security scan detected malicious content")
        await self.artifact_store.put(self.tenant_id, skill_id, version, archive)
        await self._audit(actor, skill_id, "install", "installed")
        return manifest

    async def load(
        self, *, skill_id: str, version: int, principal: Any,
        tenant_id: str | None = None,
    ) -> LoadedSkill:
        principal_tenant = getattr(principal, "tenant_id", None)
        requested_tenant = tenant_id or principal_tenant
        if requested_tenant != self.tenant_id or principal_tenant != self.tenant_id:
            raise PermissionError("Skill load tenant mismatch")
        archive = await self.artifact_store.get(self.tenant_id, skill_id, version)
        if archive is None:
            raise LookupError("Skill version is not installed")
        paths = self._inspect_archive(archive)
        # Re-scan at load time so a newly detected threat blocks existing installs.
        try:
            clean = await self.scanner.scan(skill_id + ".zip", "application/zip", archive)
        except Exception as exc:
            await self._audit(principal, skill_id, "load", "scan_error")
            raise SkillSandboxError("Skill security re-scan failed; load denied") from exc
        if clean is not True:
            await self._audit(principal, skill_id, "load", "infected")
            raise SkillSandboxError("Skill security re-scan detected malicious content")
        request = json.dumps({
            "archive": base64.b64encode(archive).decode("ascii"),
            "bundle_paths": list(paths),
        }, separators=(",", ":")).encode("utf-8")
        await self._audit(principal, skill_id, "load", "requested")
        result = await self.sandbox_runner.run(
            ("python", "-I", "-c", _BOOTSTRAP),
            stdin=request, timeout_seconds=self.timeout_seconds,
        )
        if result.timed_out:
            await self._audit(principal, skill_id, "load", "timed_out")
            raise TimeoutError("Skill sandbox load timed out")
        if result.returncode != 0 or result.output_truncated:
            await self._audit(principal, skill_id, "load", "failed")
            raise SkillSandboxError("Skill sandbox could not load the bundle")
        try:
            response = json.loads(result.stdout.decode("utf-8"))
            document = response["skill_md"]
            manifest = parse_skill_document(document, available_tools=self.available_tools)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            await self._audit(principal, skill_id, "load", "invalid_output")
            raise SkillSandboxError("Skill sandbox returned invalid bundle metadata") from exc
        if manifest.name != skill_id:
            raise SkillSandboxError("loaded Skill identity does not match requested id")
        await self._audit(principal, skill_id, "load", "loaded")
        return LoadedSkill(self.tenant_id, skill_id, version, manifest, manifest.instructions, paths)

    async def uninstall(
        self, *, skill_id: str, version: int, principal: Any,
        tenant_id: str | None = None,
    ) -> None:
        principal_tenant = getattr(principal, "tenant_id", None)
        if (tenant_id or principal_tenant) != self.tenant_id or principal_tenant != self.tenant_id:
            raise PermissionError("Skill uninstall tenant mismatch")
        await self.artifact_store.delete(self.tenant_id, skill_id, version)
        await self._audit(principal, skill_id, "uninstall", "uninstalled")
