"""Declarative extension manifest validation; execution remains in platform gateways."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

_SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_EXTENSION_ID = re.compile(r"[a-z][a-z0-9-]{1,62}\Z")
_PERMISSION = re.compile(r"[a-z][a-z0-9_.:-]{1,127}\Z")


@dataclass(frozen=True)
class ExtensionManifest:
    extension_id: str
    version: str
    platform_api: str
    permissions: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    entrypoint: str = ""
    dependency_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _EXTENSION_ID.fullmatch(self.extension_id):
            raise ValueError("invalid extension_id")
        if not _SEMVER.fullmatch(self.version):
            raise ValueError("extension version must be semantic x.y.z")
        if not _SEMVER.fullmatch(self.platform_api):
            raise ValueError("platform_api must be a semantic x.y.z version")
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("extension permissions must be unique")
        if any(not _PERMISSION.fullmatch(item) for item in self.permissions):
            raise ValueError("invalid extension permission")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("extension dependencies must be unique")
        if any(not _EXTENSION_ID.fullmatch(item) for item in self.dependencies):
            raise ValueError("invalid extension dependency")
        if self.extension_id in self.dependencies:
            raise ValueError("extension cannot depend on itself")
        if set(self.dependency_versions) != set(self.dependencies):
            raise ValueError("every extension dependency must have an exact version pin")
        if any(not _SEMVER.fullmatch(version) for version in self.dependency_versions.values()):
            raise ValueError("dependency versions must be exact semantic x.y.z versions")
        if not self.entrypoint or ".." in self.entrypoint.split("/") or self.entrypoint.startswith("/"):
            raise ValueError("entrypoint must be a safe relative path")


def validate_platform_compatibility(manifest: ExtensionManifest, *,
                                    platform_api_version: str) -> None:
    if not _SEMVER.fullmatch(platform_api_version):
        raise ValueError("platform API version must be semantic x.y.z")
    if manifest.platform_api != platform_api_version:
        raise ValueError("extension platform API version is incompatible")
