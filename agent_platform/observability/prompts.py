"""Versioned prompt templates with variable validation and deterministic canaries."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from string import Formatter

_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class PromptVersion:
    prompt_id: str
    version: int
    template: str
    variables: tuple[str, ...]
    content_hash: str


class PromptRegistry:
    """In-memory registry contract; persistence can be supplied by a later store adapter."""

    def __init__(self) -> None:
        self._versions: dict[str, dict[int, PromptVersion]] = {}
        self._active: dict[str, int] = {}
        self._canary: dict[str, tuple[int, float]] = {}

    @staticmethod
    def _variables(template: str) -> tuple[str, ...]:
        names: set[str] = set()
        try:
            for _, field_name, format_spec, conversion in Formatter().parse(template):
                if field_name is None:
                    continue
                if not _VARIABLE.fullmatch(field_name) or format_spec or conversion:
                    raise ValueError("prompt placeholders must be simple variable names")
                names.add(field_name)
        except ValueError as exc:
            raise ValueError(f"invalid prompt template: {exc}") from exc
        return tuple(sorted(names))

    def register(self, prompt_id: str, template: str) -> PromptVersion:
        if not prompt_id.strip() or not template.strip():
            raise ValueError("prompt_id and template are required")
        versions = self._versions.setdefault(prompt_id, {})
        version = max(versions, default=0) + 1
        variables = self._variables(template)
        content_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
        record = PromptVersion(prompt_id, version, template, variables, content_hash)
        versions[version] = record
        return record

    def activate(self, prompt_id: str, version: int) -> None:
        if version not in self._versions.get(prompt_id, {}):
            raise KeyError("prompt version does not exist")
        self._active[prompt_id] = version
        self._canary.pop(prompt_id, None)

    def set_canary(self, prompt_id: str, candidate_version: int, percentage: float) -> None:
        if prompt_id not in self._active:
            raise ValueError("activate a stable prompt version before setting a canary")
        if candidate_version not in self._versions.get(prompt_id, {}):
            raise KeyError("candidate prompt version does not exist")
        if candidate_version == self._active[prompt_id]:
            raise ValueError("canary candidate must differ from active version")
        if not 0 < percentage <= 100:
            raise ValueError("canary percentage must be in (0, 100]")
        self._canary[prompt_id] = (candidate_version, percentage)

    def resolve(self, prompt_id: str, *, rollout_key: str = "", version: int | None = None) -> PromptVersion:
        versions = self._versions.get(prompt_id, {})
        selected = version if version is not None else self._active.get(prompt_id)
        if selected is None:
            raise KeyError("prompt has no active version")
        if version is None and prompt_id in self._canary:
            candidate, percentage = self._canary[prompt_id]
            bucket = int(hashlib.sha256(rollout_key.encode("utf-8")).hexdigest()[:8], 16) / 0x100000000 * 100
            if bucket < percentage:
                selected = candidate
        try:
            return versions[selected]
        except KeyError as exc:
            raise KeyError("prompt version does not exist") from exc

    def render(self, prompt_id: str, variables: dict[str, str], *, rollout_key: str = "", version: int | None = None) -> tuple[str, PromptVersion]:
        prompt = self.resolve(prompt_id, rollout_key=rollout_key, version=version)
        supplied = set(variables)
        expected = set(prompt.variables)
        if supplied != expected:
            missing = sorted(expected - supplied)
            extra = sorted(supplied - expected)
            raise ValueError(f"prompt variables mismatch; missing={missing}, extra={extra}")
        return prompt.template.format_map(variables), prompt


def tenant_prompt_key(tenant_id: str, prompt_id: str) -> str:
    """Namespace prompt identifiers because the legacy registry schema is global."""
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id is required")
    if not isinstance(prompt_id, str) or not prompt_id.strip():
        raise ValueError("prompt_id is required")
    return f"tenant:{len(tenant_id)}:{tenant_id}:{prompt_id}"
