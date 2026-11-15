"""Safe parser and validator for declarative Agent Skills bundles.

Skill content is instruction data only. This module never imports, evaluates,
or executes Python/JavaScript from a bundle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping

import yaml


class SkillManifestError(ValueError):
    pass


_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PINNED = re.compile(r"^(?:==[A-Za-z0-9.+_-]+|@sha256:[a-f0-9]{64})$")


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    dependencies: Mapping[str, str]
    instructions: str


def parse_skill_document(
    content: str, *, available_tools: set[str], max_bytes: int = 256 * 1024,
) -> SkillManifest:
    if not isinstance(content, str) or not content.strip():
        raise SkillManifestError("SKILL.md must not be empty")
    if len(content.encode("utf-8")) > max_bytes:
        raise SkillManifestError("SKILL.md exceeds size limit")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillManifestError("SKILL.md must begin with YAML frontmatter")
    try:
        closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise SkillManifestError("YAML frontmatter closing delimiter is missing") from exc
    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        raise SkillManifestError("invalid YAML frontmatter") from exc
    if not isinstance(metadata, dict):
        raise SkillManifestError("frontmatter must be a YAML object")
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or len(name) > 64 or not _NAME.fullmatch(name):
        raise SkillManifestError("name must be lowercase kebab-case and at most 64 characters")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise SkillManifestError("description must contain 1 to 1024 characters")
    raw_tools = metadata.get("allowed-tools", [])
    if isinstance(raw_tools, str):
        tools = tuple(sorted(set(raw_tools.split())))
    elif isinstance(raw_tools, list) and all(isinstance(item, str) for item in raw_tools):
        tools = tuple(sorted(set(item.strip() for item in raw_tools if item.strip())))
    else:
        raise SkillManifestError("allowed-tools must be a string or string array")
    unknown = set(tools) - available_tools
    if unknown:
        raise SkillManifestError("Skill references unregistered tools: " + ", ".join(sorted(unknown)))
    extra = metadata.get("metadata", {})
    if not isinstance(extra, dict):
        raise SkillManifestError("metadata must be a YAML object")
    dependencies = extra.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise SkillManifestError("metadata.dependencies must be an object")
    normalized: dict[str, str] = {}
    for package, pin in dependencies.items():
        if (
            not isinstance(package, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", package)
            or not isinstance(pin, str) or not _PINNED.fullmatch(pin)
        ):
            raise SkillManifestError("dependencies must use exact == versions or sha256 pins")
        normalized[package] = pin
    instructions = "\n".join(lines[closing + 1:]).strip()
    if not instructions:
        raise SkillManifestError("Skill instructions body must not be empty")
    return SkillManifest(
        name=name, description=description.strip(), allowed_tools=tools,
        dependencies=normalized, instructions=instructions,
    )


def validate_skill_bundle_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Reject absolute paths, traversal, empty names and duplicate normalized paths."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not isinstance(raw, str) or not raw or "\\" in raw:
            raise SkillManifestError("bundle paths must be non-empty POSIX paths")
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise SkillManifestError("bundle path traversal is not allowed")
        value = str(path)
        if value in seen:
            raise SkillManifestError("duplicate bundle path")
        seen.add(value)
        normalized.append(value)
    if "SKILL.md" not in seen:
        raise SkillManifestError("bundle must contain SKILL.md at its root")
    return tuple(sorted(normalized))
