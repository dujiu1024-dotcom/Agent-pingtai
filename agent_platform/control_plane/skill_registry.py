"""Versioned Skill package registry and per-Agent installation orchestration.

Package bytes are stored through an injected immutable artifact registry (the
same boundary used for container/image registration); this module never runs
package code. Runtime installation is delegated to the isolated Skill runtime.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Protocol

import yaml

from agent_platform.control_plane.registry import (
    RegistryNotFound,
    RegistryService,
    RegistryVersion,
)
from agent_platform.control_plane.skill_dependency_review import (
    SkillDependencyReviewer,
    SkillDependencyReviewError,
)


class SkillPackageError(ValueError):
    """Invalid or unsafe Skill package or lifecycle operation."""


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while parsing Skill manifest", node.start_mark,
                f"duplicate key: {key}", key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_DEP_PIN = re.compile(r"^(?:==[A-Za-z0-9.+_-]+|@sha256:[a-f0-9]{64})$")
_ALLOWED_FIELDS = {"name", "version", "description", "entrypoint", "dependencies", "allowed-tools"}


@dataclass(frozen=True)
class SkillPackageManifest:
    name: str
    version: str
    description: str
    entrypoint: str
    dependencies: Mapping[str, str]
    allowed_tools: tuple[str, ...]


def _safe_package_path(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise SkillPackageError(f"{field} must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise SkillPackageError(f"{field} must be a safe relative POSIX path")
    if ":" in value.split("/")[0]:
        raise SkillPackageError(f"{field} must not be drive-qualified")
    return path.as_posix()


def parse_skill_package_manifest(content: str, *, available_tools: set[str]) -> SkillPackageManifest:
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 64 * 1024:
        raise SkillPackageError("manifest.yaml must contain at most 64 KiB of UTF-8 YAML")
    try:
        data = yaml.load(content, Loader=_UniqueLoader)
    except yaml.YAMLError as exc:
        raise SkillPackageError("manifest.yaml is invalid YAML") from exc
    if not isinstance(data, dict):
        raise SkillPackageError("manifest.yaml must be a YAML object")
    unknown = set(data) - _ALLOWED_FIELDS
    missing = _ALLOWED_FIELDS - set(data)
    if unknown or missing:
        raise SkillPackageError(
            "manifest.yaml fields mismatch; missing=" + ",".join(sorted(missing)) +
            "; unknown=" + ",".join(sorted(unknown))
        )
    name, version, description = data["name"], data["version"], data["description"]
    if not isinstance(name, str) or not _ID.fullmatch(name) or len(name) > 64:
        raise SkillPackageError("name must be lowercase kebab-case")
    if not isinstance(version, str) or not _SEMVER.fullmatch(version):
        raise SkillPackageError("version must be semantic x.y.z")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise SkillPackageError("description must contain 1 to 1024 characters")
    entrypoint = _safe_package_path(data["entrypoint"], field="entrypoint")
    raw_dependencies = data["dependencies"]
    if not isinstance(raw_dependencies, dict):
        raise SkillPackageError("dependencies must be a mapping of exact version pins")
    dependencies: dict[str, str] = {}
    for package, pin in raw_dependencies.items():
        if (not isinstance(package, str) or not _ID.fullmatch(package)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", package)
                or package in {".", ".."} or not isinstance(pin, str) or not _DEP_PIN.fullmatch(pin)):
            raise SkillPackageError("dependencies must use exact == versions or @sha256 pins")
        dependencies[package] = pin
    raw_tools = data["allowed-tools"]
    if not isinstance(raw_tools, list) or any(not isinstance(item, str) or not item for item in raw_tools):
        raise SkillPackageError("allowed-tools must be a string array")
    tools = tuple(sorted(set(raw_tools)))
    unknown_tools = set(tools) - available_tools
    if unknown_tools:
        raise SkillPackageError("Skill references unregistered tools: " + ", ".join(sorted(unknown_tools)))
    return SkillPackageManifest(name, version, description.strip(), entrypoint, dependencies, tools)


class SkillArtifactRegistry(Protocol):
    """Adapter to the platform's immutable container/artifact registry (#18)."""
    async def push(self, *, tenant_id: str, skill_id: str, version: str, content: bytes) -> str: ...
    async def pull(self, artifact_ref: str) -> bytes: ...
    async def delete(self, artifact_ref: str) -> None: ...


class AgentSkillStore(Protocol):
    async def get(self, tenant_id: str, agent_id: str, skill_id: str) -> "AgentSkillInstallation | None": ...
    async def put(self, item: "AgentSkillInstallation") -> None: ...
    async def remove(self, tenant_id: str, agent_id: str, skill_id: str) -> None: ...


class AgentSkillRuntime(Protocol):
    async def install(self, *, tenant_id: str, agent_id: str, skill_id: str,
                      version: int, artifact_ref: str) -> None: ...
    async def uninstall(self, *, tenant_id: str, agent_id: str, skill_id: str) -> None: ...


@dataclass(frozen=True)
class AgentSkillInstallation:
    tenant_id: str
    agent_id: str
    skill_id: str
    registry_version: int
    package_version: str
    artifact_ref: str


class InMemoryAgentSkillStore:
    """Test adapter; production callers inject the tenant-scoped durable Agent store."""
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], AgentSkillInstallation] = {}

    async def get(self, tenant_id: str, agent_id: str, skill_id: str) -> AgentSkillInstallation | None:
        return self._items.get((tenant_id, agent_id, skill_id))

    async def put(self, item: AgentSkillInstallation) -> None:
        self._items[(item.tenant_id, item.agent_id, item.skill_id)] = item

    async def remove(self, tenant_id: str, agent_id: str, skill_id: str) -> None:
        self._items.pop((tenant_id, agent_id, skill_id), None)


class SkillPackageRegistry:
    """Upload, validate, publish and install versioned declarative Skill bundles."""
    def __init__(
        self, *, registry: RegistryService, artifacts: SkillArtifactRegistry,
        dependency_reviewer: SkillDependencyReviewer, available_tools: set[str],
        agent_store: AgentSkillStore, agent_runtime: AgentSkillRuntime,
        audit: Any = None, max_archive_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if not 1 <= max_archive_bytes <= 8 * 1024 * 1024:
            raise ValueError("max_archive_bytes must be between 1 byte and 8 MiB")
        self.registry = registry
        self.artifacts = artifacts
        self.dependency_reviewer = dependency_reviewer
        self.available_tools = set(available_tools)
        self.agent_store = agent_store
        self.agent_runtime = agent_runtime
        self.audit = audit
        self.max_archive_bytes = max_archive_bytes
        self._lock = asyncio.Lock()

    async def _audit(self, actor: str, tenant: str, skill: str, action: str, outcome: str) -> None:
        if self.audit is None:
            return
        value = self.audit(actor, tenant, skill, action, outcome)
        if inspect.isawaitable(value):
            await value

    def _inspect(self, archive: bytes) -> tuple[SkillPackageManifest, tuple[str, ...], str]:
        if not isinstance(archive, bytes) or not archive or len(archive) > self.max_archive_bytes:
            raise SkillPackageError("Skill package is empty or exceeds the archive size limit")
        try:
            with zipfile.ZipFile(io.BytesIO(archive), "r") as package:
                members = package.infolist()
                if not members or len(members) > 256:
                    raise SkillPackageError("Skill package must contain 1 to 256 members")
                seen: set[str] = set()
                files: set[str] = set()
                total = 0
                for info in members:
                    path = _safe_package_path(info.filename.rstrip("/") if info.is_dir() else info.filename,
                                              field="archive member")
                    key = path.casefold()
                    if key in seen:
                        raise SkillPackageError("Skill package contains duplicate paths")
                    seen.add(key)
                    mode = info.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if stat.S_ISLNK(mode) or kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                        raise SkillPackageError("Skill package contains a link, special or encrypted file")
                    if not info.is_dir():
                        total += info.file_size
                        if total > self.max_archive_bytes:
                            raise SkillPackageError("Skill package expands beyond the size limit")
                        files.add(path)
                if "manifest.yaml" not in files:
                    raise SkillPackageError("Skill package must contain root manifest.yaml")
                try:
                    manifest_text = package.read("manifest.yaml").decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise SkillPackageError("manifest.yaml must be UTF-8") from exc
                manifest = parse_skill_package_manifest(manifest_text, available_tools=self.available_tools)
                if manifest.entrypoint not in files:
                    raise SkillPackageError("manifest entrypoint does not exist in the package")
                digest = hashlib.sha256(archive).hexdigest()
                return manifest, tuple(sorted(files)), digest
        except (OSError, zipfile.BadZipFile) as exc:
            raise SkillPackageError("Skill package is not a valid ZIP archive") from exc

    async def upload(self, *, tenant_id: str, archive: bytes, actor_id: str) -> RegistryVersion:
        if not tenant_id.strip() or not actor_id.strip():
            raise ValueError("tenant_id and actor_id are required")
        manifest, paths, digest = self._inspect(archive)
        async with self._lock:
            existing = await self.registry.repository.list_versions(tenant_id, "skill", manifest.name)
            if any(
                item.definition.get("manifest", {}).get("version") == manifest.version
                for item in existing
            ):
                raise SkillPackageError("this Skill package version is already registered")
            await self._audit(actor_id, tenant_id, manifest.name, "upload", "requested")
            artifact_ref = await self.artifacts.push(
                tenant_id=tenant_id, skill_id=manifest.name, version=manifest.version, content=archive,
            )
            if not isinstance(artifact_ref, str) or not artifact_ref.strip():
                raise SkillPackageError("artifact registry did not return an immutable reference")
            try:
                draft = await self.registry.create_draft(
                    tenant_id=tenant_id, kind="skill", item_id=manifest.name, actor_id=actor_id,
                    definition={
                        "format": "skill-package-v1", "manifest": {
                            "name": manifest.name, "version": manifest.version,
                            "description": manifest.description, "entrypoint": manifest.entrypoint,
                            "dependencies": dict(manifest.dependencies),
                            "allowed_tools": list(manifest.allowed_tools),
                        },
                        "files": list(paths), "sha256": digest, "artifact_ref": artifact_ref,
                    },
                )
            except Exception:
                await self.artifacts.delete(artifact_ref)
                await self._audit(actor_id, tenant_id, manifest.name, "upload", "failed")
                raise
            await self._audit(actor_id, tenant_id, manifest.name, "upload", "succeeded")
            return draft

    async def publish(self, *, tenant_id: str, skill_id: str, draft_version: int,
                      actor_id: str) -> RegistryVersion:
        async with self._lock:
            draft = await self.registry.repository.get(tenant_id, "skill", skill_id, draft_version)
            if draft is None or draft.status != "draft":
                raise RegistryNotFound("Skill draft not found")
            manifest = draft.definition.get("manifest", {})
            if not isinstance(manifest, Mapping):
                raise SkillPackageError("stored Skill manifest is invalid")
            # Preflight the exact dependency pins before changing the active registry pointer.
            candidate = RegistryVersion(
                tenant_id=draft.tenant_id, kind=draft.kind, item_id=draft.item_id,
                version=draft.version, definition=draft.definition, status="published",
                created_by=draft.created_by, created_at=draft.created_at,
            )
            review = await self.dependency_reviewer.review(item=candidate, actor_id=actor_id)
            if review.status != "approved":
                await self._audit(actor_id, tenant_id, skill_id, "publish", "dependency_rejected")
                raise SkillDependencyReviewError(
                    "Skill dependency validation rejected: " + ", ".join(review.findings)
                )
            published = await self.registry.publish(
                tenant_id=tenant_id, kind="skill", item_id=skill_id, version=draft_version,
            )
            # RegistryService creates a new immutable version on publish; bind the
            # preflight approval to that exact published version and same manifest.
            from dataclasses import replace
            await self.dependency_reviewer.store.save(replace(review, skill_version=published.version))
            await self._audit(actor_id, tenant_id, skill_id, "publish", "succeeded")
            return published

    async def install_for_agent(self, *, tenant_id: str, agent_id: str, skill_id: str,
                                version: int, actor_id: str) -> AgentSkillInstallation:
        if not tenant_id.strip() or not agent_id.strip() or not actor_id.strip():
            raise ValueError("tenant_id, agent_id and actor_id are required")
        async with self._lock:
            item = await self.registry.repository.get(tenant_id, "skill", skill_id, version)
            if item is None or item.status != "published":
                raise RegistryNotFound("published Skill version not found")
            await self.dependency_reviewer.require_approved(tenant_id=tenant_id, item=item)
            manifest = item.definition.get("manifest", {})
            artifact_ref = item.definition.get("artifact_ref")
            if not isinstance(artifact_ref, str) or not artifact_ref:
                raise SkillPackageError("published Skill has no immutable artifact reference")
            previous = await self.agent_store.get(tenant_id, agent_id, skill_id)
            installation = AgentSkillInstallation(
                tenant_id, agent_id, skill_id, version, str(manifest.get("version", "")), artifact_ref,
            )
            await self._audit(actor_id, tenant_id, skill_id, "install", "requested")
            await self.agent_runtime.install(
                tenant_id=tenant_id, agent_id=agent_id, skill_id=skill_id,
                version=version, artifact_ref=artifact_ref,
            )
            try:
                await self.agent_store.put(installation)
                await self._audit(actor_id, tenant_id, skill_id, "install", "succeeded")
            except Exception:
                try:
                    if previous is None:
                        await self.agent_runtime.uninstall(
                            tenant_id=tenant_id, agent_id=agent_id, skill_id=skill_id,
                        )
                        await self.agent_store.remove(tenant_id, agent_id, skill_id)
                    else:
                        await self.agent_runtime.install(
                            tenant_id=tenant_id, agent_id=agent_id, skill_id=skill_id,
                            version=previous.registry_version, artifact_ref=previous.artifact_ref,
                        )
                        await self.agent_store.put(previous)
                finally:
                    await self._audit(actor_id, tenant_id, skill_id, "install", "compensated")
                raise
            return installation

    async def uninstall_from_agent(self, *, tenant_id: str, agent_id: str,
                                   skill_id: str, actor_id: str) -> None:
        async with self._lock:
            current = await self.agent_store.get(tenant_id, agent_id, skill_id)
            if current is None:
                raise RegistryNotFound("Skill is not installed for this Agent")
            await self._audit(actor_id, tenant_id, skill_id, "uninstall", "requested")
            await self.agent_runtime.uninstall(
                tenant_id=tenant_id, agent_id=agent_id, skill_id=skill_id,
            )
            try:
                await self.agent_store.remove(tenant_id, agent_id, skill_id)
                await self._audit(actor_id, tenant_id, skill_id, "uninstall", "succeeded")
            except Exception:
                await self.agent_runtime.install(
                    tenant_id=tenant_id, agent_id=agent_id, skill_id=skill_id,
                    version=current.registry_version, artifact_ref=current.artifact_ref,
                )
                await self.agent_store.put(current)
                await self._audit(actor_id, tenant_id, skill_id, "uninstall", "compensated")
                raise
