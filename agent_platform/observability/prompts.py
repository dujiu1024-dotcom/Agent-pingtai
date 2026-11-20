"""Versioned prompt templates with variable validation and deterministic canaries."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from string import Formatter

_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class PromptVariant:
    prompt_id: str
    name: str
    current_version: int
    status: str
    created_by: str
    updated_by: str


@dataclass(frozen=True)
class PromptVersion:
    prompt_id: str
    version: int
    template: str
    variables: tuple[str, ...]
    content_hash: str


@dataclass(frozen=True)
class PromptAuditEvent:
    event_id: int
    prompt_id: str
    action: str
    actor_id: str
    details: tuple[tuple[str, str | int | float | None], ...]
    created_at: datetime


class PromptRegistry:
    """In-memory registry contract; persistence can be supplied by a later store adapter."""

    def __init__(self) -> None:
        self._versions: dict[str, dict[int, PromptVersion]] = {}
        self._active: dict[str, int] = {}
        self._canary: dict[str, tuple[int, float]] = {}
        self._variants: dict[str, dict[str, PromptVariant]] = {}
        self._variant_history: dict[tuple[str, str], list[int]] = {}
        self._audit_events: list[PromptAuditEvent] = []

    def _record_audit(
        self, prompt_id: str, action: str, actor_id: str,
        details: dict[str, str | int | float | None],
    ) -> None:
        self._audit_events.append(PromptAuditEvent(
            event_id=len(self._audit_events) + 1,
            prompt_id=prompt_id,
            action=action,
            actor_id=actor_id,
            details=tuple(sorted(details.items())),
            created_at=datetime.now(timezone.utc),
        ))

    def list_audit(self, prompt_id: str, *, limit: int = 100) -> tuple[PromptAuditEvent, ...]:
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ValueError("prompt_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        events = [event for event in self._audit_events if event.prompt_id == prompt_id]
        return tuple(events[-limit:])

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

    def register(
        self, prompt_id: str, template: str, *, created_by: str = "system",
    ) -> PromptVersion:
        if (
            not isinstance(prompt_id, str) or not prompt_id.strip()
            or not isinstance(template, str) or not template.strip()
        ):
            raise ValueError("prompt_id and template are required")
        if not isinstance(created_by, str) or not created_by.strip():
            raise ValueError("created_by is required")
        versions = self._versions.setdefault(prompt_id, {})
        version = max(versions, default=0) + 1
        variables = self._variables(template)
        content_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
        record = PromptVersion(prompt_id, version, template, variables, content_hash)
        versions[version] = record
        self._record_audit(prompt_id, "version.registered", created_by, {
            "version": version, "content_hash": content_hash,
        })
        return record

    def list_prompts(self, *, prefix: str, limit: int = 50) -> tuple[dict[str, object], ...]:
        """List prompt summaries scoped to an exact tenant storage-key prefix."""
        if not isinstance(prefix, str) or not prefix.endswith(":"):
            raise ValueError("tenant prompt prefix must end with a separator")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        summaries: list[dict[str, object]] = []
        for storage_key, versions in self._versions.items():
            if not storage_key.startswith(prefix) or not versions:
                continue
            latest_version = max(versions)
            latest = versions[latest_version]
            canary = self._canary.get(storage_key)
            summaries.append({
                "prompt_id": storage_key[len(prefix):],
                "latest_version": latest_version,
                "active_version": self._active.get(storage_key),
                "canary_version": canary[0] if canary else None,
                "canary_percentage": canary[1] if canary else None,
                "versions_count": len(versions),
                "variables": list(latest.variables),
                "content_hash": latest.content_hash,
            })
        summaries.sort(key=lambda item: str(item["prompt_id"]))
        return tuple(summaries[:limit])

    def list_versions(self, prompt_id: str) -> tuple[PromptVersion, ...]:
        """Return immutable versions in ascending version order."""
        return tuple(
            self._versions.get(prompt_id, {})[number]
            for number in sorted(self._versions.get(prompt_id, {}))
        )

    @staticmethod
    def _validate_variant_name(name: str) -> str:
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise ValueError("variant name must be a lowercase identifier")
        return name

    def create_variant(
        self, prompt_id: str, name: str, version: int, *, created_by: str,
    ) -> PromptVariant:
        name = self._validate_variant_name(name)
        if not isinstance(created_by, str) or not created_by.strip():
            raise ValueError("created_by is required")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        if version not in self._versions.get(prompt_id, {}):
            raise KeyError("prompt version does not exist")
        variants = self._variants.setdefault(prompt_id, {})
        if name in variants:
            raise ValueError("prompt variant already exists")
        variant = PromptVariant(prompt_id, name, version, "active", created_by, created_by)
        variants[name] = variant
        self._variant_history[(prompt_id, name)] = [version]
        self._record_audit(prompt_id, "variant.created", created_by, {
            "variant": name, "version": version,
        })
        return variant

    def assign_variant(
        self, prompt_id: str, name: str, version: int, *, updated_by: str,
    ) -> PromptVariant:
        name = self._validate_variant_name(name)
        if not isinstance(updated_by, str) or not updated_by.strip():
            raise ValueError("updated_by is required")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        variants = self._variants.get(prompt_id, {})
        variant = variants.get(name)
        if variant is None:
            raise KeyError("prompt variant does not exist")
        if variant.status != "active":
            raise ValueError("deprecated prompt variant cannot be changed")
        if version not in self._versions.get(prompt_id, {}):
            raise KeyError("prompt version does not exist")
        updated = PromptVariant(
            prompt_id, name, version, variant.status, variant.created_by, updated_by,
        )
        variants[name] = updated
        history = self._variant_history[(prompt_id, name)]
        if not history or history[-1] != version:
            history.append(version)
        self._record_audit(prompt_id, "variant.version_assigned", updated_by, {
            "variant": name, "previous_version": variant.current_version,
            "version": version,
        })
        return updated

    def deprecate_variant(
        self, prompt_id: str, name: str, *, updated_by: str,
    ) -> PromptVariant:
        name = self._validate_variant_name(name)
        if not isinstance(updated_by, str) or not updated_by.strip():
            raise ValueError("updated_by is required")
        variants = self._variants.get(prompt_id, {})
        variant = variants.get(name)
        if variant is None:
            raise KeyError("prompt variant does not exist")
        if variant.status == "deprecated":
            return variant
        canary = self._canary.get(prompt_id)
        if (
            variant.current_version == self._active.get(prompt_id)
            or (canary is not None and variant.current_version == canary[0])
        ):
            raise ValueError("deployed prompt variant cannot be deprecated")
        updated = PromptVariant(
            prompt_id, name, variant.current_version, "deprecated",
            variant.created_by, updated_by,
        )
        variants[name] = updated
        self._record_audit(prompt_id, "variant.deprecated", updated_by, {
            "variant": name, "version": variant.current_version,
        })
        return updated

    def list_variants(self, prompt_id: str) -> tuple[PromptVariant, ...]:
        return tuple(
            self._variants.get(prompt_id, {})[name]
            for name in sorted(self._variants.get(prompt_id, {}))
        )

    def variant_history(self, prompt_id: str, name: str) -> tuple[int, ...]:
        name = self._validate_variant_name(name)
        if name not in self._variants.get(prompt_id, {}):
            raise KeyError("prompt variant does not exist")
        return tuple(self._variant_history[(prompt_id, name)])

    def activate(
        self, prompt_id: str, version: int, *, updated_by: str = "system",
        evaluation_run_id: str | None = None,
    ) -> None:
        if not isinstance(updated_by, str) or not updated_by.strip():
            raise ValueError("updated_by is required")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        if version not in self._versions.get(prompt_id, {}):
            raise KeyError("prompt version does not exist")
        self._active[prompt_id] = version
        self._canary.pop(prompt_id, None)
        self._record_audit(prompt_id, "version.activated", updated_by, {
            "active_version": version, "evaluation_run_id": evaluation_run_id,
        })

    def rollback(
        self, prompt_id: str, version: int, *, updated_by: str,
        evaluation_run_id: str | None = None,
    ) -> None:
        if not isinstance(updated_by, str) or not updated_by.strip():
            raise ValueError("updated_by is required")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        if version not in self._versions.get(prompt_id, {}):
            raise KeyError("prompt version does not exist")
        self._active[prompt_id] = version
        self._canary.pop(prompt_id, None)
        self._record_audit(prompt_id, "version.rolled_back", updated_by, {
            "active_version": version, "evaluation_run_id": evaluation_run_id,
        })

    def set_canary(
        self, prompt_id: str, candidate_version: int, percentage: float, *,
        updated_by: str = "system", evaluation_run_id: str | None = None,
    ) -> None:
        if prompt_id not in self._active:
            raise ValueError("activate a stable prompt version before setting a canary")
        if candidate_version not in self._versions.get(prompt_id, {}):
            raise KeyError("candidate prompt version does not exist")
        if candidate_version == self._active[prompt_id]:
            raise ValueError("canary candidate must differ from active version")
        if not 0 < percentage <= 100:
            raise ValueError("canary percentage must be in (0, 100]")
        self._canary[prompt_id] = (candidate_version, percentage)
        self._record_audit(prompt_id, "canary.configured", updated_by, {
            "canary_version": candidate_version, "percentage": percentage,
            "evaluation_run_id": evaluation_run_id,
        })

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
