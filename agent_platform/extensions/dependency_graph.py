"""Deterministic dependency planning and safe uninstall impact analysis."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

from agent_platform.extensions.manifest import ExtensionManifest


def resolve_install_order(manifests: Iterable[ExtensionManifest]) -> tuple[str, ...]:
    """Return dependencies before dependants; reject missing deps and cycles."""
    records = tuple(manifests)
    by_id = {item.extension_id: item for item in records}
    if len(by_id) != len(records):
        raise ValueError("duplicate extension id")
    indegree = {key: 0 for key in by_id}
    dependants: dict[str, set[str]] = defaultdict(set)
    for extension_id, manifest in by_id.items():
        for dependency in manifest.dependencies:
            if dependency not in by_id:
                raise ValueError(f"missing dependency: {dependency}")
            indegree[extension_id] += 1
            dependants[dependency].add(extension_id)
    ready = deque(sorted(key for key, count in indegree.items() if count == 0))
    ordered: list[str] = []
    while ready:
        current = ready.popleft()
        ordered.append(current)
        for child in sorted(dependants[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(ordered) != len(by_id):
        raise ValueError("extension dependency cycle detected")
    return tuple(ordered)


def uninstall_impact(
    extension_id: str, manifests: Iterable[ExtensionManifest]
) -> tuple[str, ...]:
    """Return installed dependants that must be removed before the requested item."""
    by_id = {item.extension_id: item for item in manifests}
    if extension_id not in by_id:
        raise KeyError(extension_id)
    reverse: dict[str, set[str]] = defaultdict(set)
    for item in by_id.values():
        for dependency in item.dependencies:
            reverse[dependency].add(item.extension_id)
    affected: set[str] = set()
    pending = [extension_id]
    while pending:
        current = pending.pop()
        for dependant in reverse[current]:
            if dependant not in affected:
                affected.add(dependant)
                pending.append(dependant)
    # Reverse install order: dependants first, target last.
    order = resolve_install_order(by_id.values())
    return tuple(item for item in reversed(order) if item == extension_id or item in affected)
