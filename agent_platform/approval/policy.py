"""Default supervised-execution policy.

This module is deliberately provider- and storage-agnostic. Approval persistence
and identity authorization are implemented by adapters in later milestones.
"""

from __future__ import annotations

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
    risk: RiskLevel
    parameters: Mapping[str, Any]
    actor_id: str
    approval_id: str | None = None


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    requires_approval: bool
    reason: str


class SupervisedPolicy:
    """Fail-closed policy for tool actions."""

    def evaluate(
        self,
        request: ActionRequest,
        *,
        approved_action_hash: str | None = None,
        requested_action_hash: str | None = None,
    ) -> PolicyResult:
        if not request.action.strip() or not request.resource.strip():
            return PolicyResult(False, False, "action and resource are required")
        if request.risk >= RiskLevel.FINANCIAL_OR_SECURITY:
            return PolicyResult(
                False, True,
                "L3 action must be executed directly by an authorized human",
            )
        if request.risk >= RiskLevel.EXTERNAL_OR_IRREVERSIBLE:
            if not request.approval_id:
                return PolicyResult(False, True, "valid human approval is required")
            if (
                not approved_action_hash
                or not requested_action_hash
                or approved_action_hash != requested_action_hash
            ):
                return PolicyResult(
                    False, True, "approval is missing, expired, or does not match action parameters"
                )
            return PolicyResult(True, False, "approved action hash matches")
        return PolicyResult(True, False, "action is within automatic execution policy")
