"""LangGraph multi-expert workflow.

LangGraph owns orchestration. The injected services own platform-specific
blackboard persistence, Harness execution, conflict resolution, and approvals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from operator import add
from typing import Annotated, Any, Protocol, TypedDict

from agent_platform.runtime.langgraph_adapter import build_state_graph


class MultiExpertState(TypedDict, total=False):
    tenant_id: str
    task_id: str
    run_id: str
    objective: str
    run_context: dict[str, Any]
    expert_assignments: list[dict[str, Any]]
    assignment: dict[str, Any]
    contributions: Annotated[list[dict[str, Any]], add]
    synthesis: dict[str, Any]
    conflicts: list[dict[str, Any]]
    requires_approval: bool
    approval_request: dict[str, Any]
    approval_result: dict[str, Any]
    final_result: dict[str, Any]
    status: str


class MultiExpertServices(Protocol):
    async def plan_experts(
        self, *, objective: str, run_context: Mapping[str, Any]
    ) -> Sequence[Mapping[str, Any]]:
        ...

    async def run_expert(
        self,
        *,
        assignment: Mapping[str, Any],
        objective: str,
        run_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    async def synthesize(
        self,
        *,
        objective: str,
        contributions: Sequence[Mapping[str, Any]],
        run_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    async def review(
        self,
        *,
        synthesis: Mapping[str, Any],
        contributions: Sequence[Mapping[str, Any]],
        run_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    async def resolve_conflicts(
        self,
        *,
        synthesis: Mapping[str, Any],
        conflicts: Sequence[Mapping[str, Any]],
        run_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    async def prepare_approval(
        self, *, state: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    async def apply_approval(
        self, *, state: Mapping[str, Any], decision: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    async def archive(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        ...

    async def reject(self, *, state: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


def route_after_review(state: Mapping[str, Any]) -> str:
    if state.get("conflicts"):
        return "resolve_conflicts"
    if state.get("requires_approval"):
        return "human_approval"
    return "archive"


def route_after_resolution(state: Mapping[str, Any]) -> str:
    return "human_approval" if state.get("requires_approval") else "archive"


def route_after_approval(state: Mapping[str, Any]) -> str:
    approval = state.get("approval_result") or {}
    return "archive" if approval.get("approved") is True else "reject"


def build_multi_expert_workflow(
    *,
    services: MultiExpertServices,
    checkpointer: Any = None,
) -> Any:
    """Compile the multi-expert graph with a caller-supplied durable checkpointer."""

    def workflow_context(state: MultiExpertState) -> dict[str, Any]:
        return {
            **state.get("run_context", {}),
            "tenant_id": state["tenant_id"],
            "task_id": state.get("task_id"),
            "run_id": state["run_id"],
        }

    async def plan(state: MultiExpertState) -> dict[str, Any]:
        assignments = await services.plan_experts(
            objective=state["objective"],
            run_context=workflow_context(state),
        )
        return {"expert_assignments": [dict(item) for item in assignments]}

    def dispatch(state: MultiExpertState) -> Any:
        try:
            from langgraph.types import Send
        except ImportError as exc:
            raise RuntimeError("Install LangGraph to run this workflow") from exc

        assignments = state.get("expert_assignments", [])
        if not assignments:
            return "synthesize"
        return [
            Send(
                "run_expert",
                {
                    "tenant_id": state["tenant_id"],
                    "task_id": state["task_id"],
                    "run_id": state["run_id"],
                    "objective": state["objective"],
                    "run_context": state["run_context"],
                    "assignment": assignment,
                },
            )
            for assignment in assignments
        ]

    async def run_expert(state: MultiExpertState) -> dict[str, Any]:
        contribution = await services.run_expert(
            assignment=state["assignment"],
            objective=state["objective"],
            run_context=state["run_context"],
        )
        return {"contributions": [dict(contribution)]}

    async def synthesize(state: MultiExpertState) -> dict[str, Any]:
        result = await services.synthesize(
            objective=state["objective"],
            contributions=state.get("contributions", []),
            run_context=state["run_context"],
        )
        return {"synthesis": dict(result)}

    async def review(state: MultiExpertState) -> dict[str, Any]:
        result = await services.review(
            synthesis=state["synthesis"],
            contributions=state.get("contributions", []),
            run_context=state["run_context"],
        )
        return {
            "conflicts": [dict(item) for item in result.get("conflicts", [])],
            "requires_approval": bool(result.get("requires_approval", False)),
        }

    async def resolve_conflicts(state: MultiExpertState) -> dict[str, Any]:
        result = await services.resolve_conflicts(
            synthesis=state["synthesis"],
            conflicts=state.get("conflicts", []),
            run_context=state["run_context"],
        )
        return {
            "synthesis": dict(result.get("synthesis", state["synthesis"])),
            "requires_approval": bool(result.get("requires_approval", False)),
        }

    async def human_approval(state: MultiExpertState) -> dict[str, Any]:
        try:
            from langgraph.types import interrupt
        except ImportError as exc:
            raise RuntimeError("Install LangGraph to run this workflow") from exc
        request = await services.prepare_approval(state=state)
        decision = interrupt(dict(request))
        result = await services.apply_approval(state=state, decision=decision)
        return {"approval_request": dict(request), "approval_result": dict(result)}

    async def archive(state: MultiExpertState) -> dict[str, Any]:
        result = await services.archive(state=state)
        return {"final_result": dict(result), "status": "completed"}

    async def reject(state: MultiExpertState) -> dict[str, Any]:
        result = await services.reject(state=state)
        return {"final_result": dict(result), "status": "rejected"}

    return build_state_graph(
        state_schema=MultiExpertState,
        nodes={
            "plan": plan,
            "run_expert": run_expert,
            "synthesize": synthesize,
            "review": review,
            "resolve_conflicts": resolve_conflicts,
            "human_approval": human_approval,
            "archive": archive,
            "reject": reject,
        },
        entry_point="plan",
        conditional_edges=(
            ("plan", dispatch, {"synthesize": "synthesize"}),
            (
                "review",
                route_after_review,
                {
                    "resolve_conflicts": "resolve_conflicts",
                    "human_approval": "human_approval",
                    "archive": "archive",
                },
            ),
            (
                "resolve_conflicts",
                route_after_resolution,
                {"human_approval": "human_approval", "archive": "archive"},
            ),
            (
                "human_approval",
                route_after_approval,
                {"archive": "archive", "reject": "reject"},
            ),
        ),
        edges=(
            ("run_expert", "synthesize"),
            ("synthesize", "review"),
            ("archive", "__end__"),
            ("reject", "__end__"),
        ),
        checkpointer=checkpointer,
    )
