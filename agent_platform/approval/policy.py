"""Fail-closed supervised-execution policy.

Risk classification comes from the trusted server-side tool registry, never
from model output or an agent-supplied argument.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping


class RiskLevel(IntEnum):
    READ_ONLY = 0
    INTERNAL_REVERSIBLE = 1
    EXTERNAL_OR_IRREVERSIBLE = 2
    FINANCIAL_OR_SECURITY = 3


@dataclass(frozen=True)
class ActionRequest:
    action: str
    resource: str
    parameters: Mapping[str, Any]
    actor_id: str
    approval_id: str | None = None

    def digest(self) -> str:
        """Stable approval binding: changing actor, target, or args invalidates it."""
        canonical = json.dumps(
            {
                "action": self.action,
                "resource": self.resource,
                "actor_id": self.actor_id,
                "parameters": dict(self.parameters),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    requires_approval: bool
    reason: str


class SupervisedPolicy:
    """Tool registry is trusted configuration; unknown tools are denied."""

    def __init__(self, tool_risks: Mapping[str, RiskLevel]) -> None:
        self._tool_risks = dict(tool_risks)

    def evaluate(
        self,
        request: ActionRequest,
        *,
        approved_action_hash: str | None = None,
    ) -> PolicyResult:
        if not request.action.strip() or not request.resource.strip() or not request.actor_id.strip():
            return PolicyResult(False, False, "action, resource, and actor_id are required")

        risk = self._tool_risks.get(request.action)
        if risk is None:
            return PolicyResult(False, False, "tool is not registered in the trusted policy registry")

        if risk >= RiskLevel.FINANCIAL_OR_SECURITY:
            return PolicyResult(
                False, True,
                "L3 action must be executed directly by an authorized human",
            )

        if risk >= RiskLevel.EXTERNAL_OR_IRREVERSIBLE:
            if not request.approval_id:
                return PolicyResult(False, True, "valid human approval is required")
            if not approved_action_hash or approved_action_hash != request.digest():
                return PolicyResult(
                    False, True,
                    "approval is missing, expired, or does not match actor, target, and parameters",
                )
            return PolicyResult(True, False, "approved action digest matches")

        return PolicyResult(True, False, "action is within automatic execution policy")
