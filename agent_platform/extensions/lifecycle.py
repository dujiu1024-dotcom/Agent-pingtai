"""Deterministic extension install/uninstall planning with dependency safety."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from agent_platform.extensions.manifest import (
    ExtensionManifest,
    validate_platform_compatibility,
)


@dataclass(frozen=True)
class ExtensionLifecyclePlan:
    action: str
    ordered_extension_ids: tuple[str, ...]
    manifests: tuple[ExtensionManifest, ...]


class ExtensionLifecycle:
    """Build side-effect-free plans; execution must happen in a trusted platform gateway."""

    def __init__(self, *, platform_api_version: str) -> None:
        self.platform_api_version = platform_api_version

    def plan_install(
        self,
        manifest: ExtensionManifest,
        *,
        installed: Mapping[str, ExtensionManifest],
        available: Mapping[str, ExtensionManifest],
    ) -> ExtensionLifecyclePlan:
        candidates = dict(available)
        candidates.update(installed)
        candidates[manifest.extension_id] = manifest
        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[ExtensionManifest] = []

        def visit(extension_id: str) -> None:
            if extension_id in visited:
                return
            if extension_id in visiting:
                raise ValueError(f"extension dependency cycle detected at {extension_id}")
            dependency = candidates.get(extension_id)
            if dependency is None:
                raise ValueError(f"missing extension dependency: {extension_id}")
            validate_platform_compatibility(
                dependency, platform_api_version=self.platform_api_version,
            )
            visiting.add(extension_id)
            for required in sorted(dependency.dependencies):
                expected_version = dependency.dependency_versions[required]
                available_version = candidates.get(required)
                if available_version is None:
                    raise ValueError(f"missing extension dependency: {required}")
                if available_version.version != expected_version:
                    raise ValueError(
                        f"extension dependency version mismatch: {required} "
                        f"requires {expected_version}, found {available_version.version}"
                    )
                visit(required)
            visiting.remove(extension_id)
            visited.add(extension_id)
            if extension_id not in installed or installed[extension_id] != dependency:
                ordered.append(dependency)

        visit(manifest.extension_id)
        return ExtensionLifecyclePlan(
            action="install",
            ordered_extension_ids=tuple(item.extension_id for item in ordered),
            manifests=tuple(ordered),
        )

    def plan_uninstall(
        self,
        extension_id: str,
        *,
        installed: Mapping[str, ExtensionManifest],
        cascade: bool = False,
    ) -> ExtensionLifecyclePlan:
        if extension_id not in installed:
            raise ValueError(f"extension is not installed: {extension_id}")
        dependents: dict[str, set[str]] = {name: set() for name in installed}
        for name, manifest in installed.items():
            for dependency in manifest.dependencies:
                if dependency in dependents:
                    dependents[dependency].add(name)

        removal: set[str] = set()

        def include(name: str) -> None:
            if name in removal:
                return
            active_dependents = dependents.get(name, set()) - removal
            if active_dependents and not cascade:
                raise ValueError(
                    f"extension {name} is required by: {', '.join(sorted(active_dependents))}"
                )
            removal.add(name)
            for dependent in sorted(active_dependents):
                include(dependent)

        include(extension_id)
        ordered_ids = self._reverse_dependency_order(removal, installed)
        return ExtensionLifecyclePlan(
            action="uninstall",
            ordered_extension_ids=ordered_ids,
            manifests=tuple(installed[name] for name in ordered_ids),
        )

    @staticmethod
    def _reverse_dependency_order(
        selected: set[str],
        installed: Mapping[str, ExtensionManifest],
    ) -> tuple[str, ...]:
        ordered: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise ValueError(f"installed extension dependency cycle detected at {name}")
            visiting.add(name)
            for dependency in sorted(installed[name].dependencies):
                if dependency in selected:
                    visit(dependency)
            visiting.remove(name)
            visited.add(name)
            ordered.append(name)

        for name in sorted(selected):
            visit(name)
        return tuple(reversed(ordered))
