"""Business workflow modeling and enterprise connector capability contracts.

This module owns business-domain definitions only. Execution, credentials, approvals,
identity and connector transports remain delegated to their existing platform services.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from fastapi import APIRouter, HTTPException, Request

NODE_TYPES = frozenset({"trigger", "condition", "branch", "parallel", "subflow", "connector_action", "approval", "end"})
TRIGGER_TYPES = frozenset({"event", "schedule", "manual"})
SYSTEM_TYPES = frozenset({"erp", "crm", "oa", "mes", "finance", "hr"})
OPERATIONS = frozenset({"read", "write", "search", "create", "update", "submit", "approve", "cancel"})


class WorkflowDefinitionError(ValueError):
    """Invalid workflow graph or connector declaration."""


@dataclass(frozen=True)
class WorkflowNode:
    node_id: str
    node_type: str
    config: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowEdge:
    source: str
    target: str
    label: str | None = None
    condition: str | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    tenant_id: str
    name: str
    version: int
    trigger_type: str
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    created_by: str
    status: str = "draft"


@dataclass(frozen=True)
class ConnectorCapability:
    connector_id: str
    tenant_id: str
    system_type: str
    provider: str
    version: str
    read_operations: tuple[str, ...]
    write_operations: tuple[str, ...]
    scopes: tuple[str, ...]
    credential_ref: str
    supports_idempotency: bool
    supports_webhooks: bool
    supports_compensation: bool = False

    def validate(self) -> None:
        if self.system_type not in SYSTEM_TYPES:
            raise WorkflowDefinitionError("unsupported enterprise system type")
        for field_name, values in (("read_operations", self.read_operations), ("write_operations", self.write_operations)):
            if len(set(values)) != len(values) or any(v not in OPERATIONS for v in values):
                raise WorkflowDefinitionError(f"{field_name} contains unsupported or duplicate operations")
        if set(self.read_operations) & set(self.write_operations):
            raise WorkflowDefinitionError("operation cannot be declared as both read and write")
        if not self.connector_id.strip() or not self.tenant_id.strip() or not self.provider.strip() or not self.version.strip():
            raise WorkflowDefinitionError("connector identity, tenant, provider and version are required")
        if not self.credential_ref.startswith(f"secret://{self.tenant_id}/"):
            raise WorkflowDefinitionError("credential_ref must be a tenant-scoped secret reference")
        if not self.scopes or any(not scope.strip() for scope in self.scopes):
            raise WorkflowDefinitionError("explicit connector scopes are required")
        if len(set(self.scopes)) != len(self.scopes):
            raise WorkflowDefinitionError("connector scopes must be unique")


def validate_workflow_graph(
    *, workflow_id: str, tenant_id: str, name: str, version: int,
    trigger_type: str, nodes: Sequence[WorkflowNode], edges: Sequence[WorkflowEdge],
    created_by: str,
) -> WorkflowDefinition:
    if not all(isinstance(v, str) and v.strip() for v in (workflow_id, tenant_id, name, created_by)):
        raise WorkflowDefinitionError("workflow identity, tenant, name and creator are required")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise WorkflowDefinitionError("workflow version must be a positive integer")
    if trigger_type not in TRIGGER_TYPES:
        raise WorkflowDefinitionError("trigger_type must be event, schedule or manual")
    if not nodes:
        raise WorkflowDefinitionError("workflow requires at least one node")
    by_id: dict[str, WorkflowNode] = {}
    for node in nodes:
        if not node.node_id or node.node_id in by_id:
            raise WorkflowDefinitionError("node ids must be non-empty and unique")
        if node.node_type not in NODE_TYPES:
            raise WorkflowDefinitionError(f"unsupported node type: {node.node_type}")
        by_id[node.node_id] = node
    triggers = [n for n in nodes if n.node_type == "trigger"]
    if len(triggers) != 1 or triggers[0].config.get("trigger_type", trigger_type) != trigger_type:
        raise WorkflowDefinitionError("workflow must contain exactly one matching trigger node")
    if not any(n.node_type == "end" for n in nodes):
        raise WorkflowDefinitionError("workflow requires at least one end node")
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    incoming: dict[str, int] = {node_id: 0 for node_id in by_id}
    seen_edges: set[tuple[str, str, str | None]] = set()
    for edge in edges:
        if edge.source not in by_id or edge.target not in by_id or edge.source == edge.target:
            raise WorkflowDefinitionError("edge references unknown node or self-loop")
        key = (edge.source, edge.target, edge.label)
        if key in seen_edges:
            raise WorkflowDefinitionError("duplicate workflow edge")
        seen_edges.add(key)
        adjacency[edge.source].append(edge.target)
        incoming[edge.target] += 1
        if by_id[edge.source].node_type == "branch" and not (edge.label or edge.condition):
            raise WorkflowDefinitionError("branch edges require a label or condition")
    if incoming[triggers[0].node_id] != 0:
        raise WorkflowDefinitionError("trigger node cannot have incoming edges")
    if any(incoming[node_id] == 0 for node_id in by_id if node_id != triggers[0].node_id):
        raise WorkflowDefinitionError("all non-trigger nodes must be connected to the workflow")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise WorkflowDefinitionError("workflow graph must be acyclic; model repetition as a subflow invocation")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child in adjacency[node_id]:
            visit(child)
        visiting.remove(node_id)
        visited.add(node_id)
    visit(triggers[0].node_id)
    if len(visited) != len(by_id):
        raise WorkflowDefinitionError("workflow contains unreachable nodes")
    if any(n.node_type == "parallel" and len(adjacency[n.node_id]) < 2 for n in nodes):
        raise WorkflowDefinitionError("parallel node requires at least two outgoing branches")
    if any(n.node_type == "subflow" and not n.config.get("workflow_id") for n in nodes):
        raise WorkflowDefinitionError("subflow node requires a referenced workflow_id")
    if any(n.node_type == "connector_action" and not all(n.config.get(k) for k in ("connector_id", "operation")) for n in nodes):
        raise WorkflowDefinitionError("connector_action requires connector_id and operation")
    return WorkflowDefinition(
        workflow_id=workflow_id, tenant_id=tenant_id, name=name, version=version,
        trigger_type=trigger_type, nodes=tuple(nodes), edges=tuple(edges), created_by=created_by,
    )


class WorkflowDefinitionRepository(Protocol):
    async def save(self, definition: WorkflowDefinition) -> WorkflowDefinition: ...
    async def get(self, tenant_id: str, workflow_id: str, version: int) -> WorkflowDefinition | None: ...


class InMemoryWorkflowDefinitionRepository:
    """Test/development repository; production composition must inject durable storage."""
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, int], WorkflowDefinition] = {}

    async def save(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        key = (definition.tenant_id, definition.workflow_id, definition.version)
        if key in self._items:
            raise WorkflowDefinitionError("workflow version is immutable")
        self._items[key] = definition
        return definition

    async def get(self, tenant_id: str, workflow_id: str, version: int) -> WorkflowDefinition | None:
        return self._items.get((tenant_id, workflow_id, version))


def connector_capability_catalog() -> tuple[dict[str, Any], ...]:
    """Capability vocabulary, not a claim that a live provider is installed."""
    profiles = {
        "erp": (("read", "search"), ("create", "update", "submit"), ("records.read", "records.write")),
        "crm": (("read", "search"), ("create", "update"), ("contacts.read", "deals.write")),
        "oa": (("read", "search"), ("submit", "approve", "cancel"), ("forms.read", "workflow.write")),
        "mes": (("read", "search"), ("create", "update", "submit"), ("orders.read", "production.write")),
        "finance": (("read", "search"), ("create", "submit", "cancel"), ("ledger.read", "payment.submit")),
        "hr": (("read", "search"), ("create", "update", "submit"), ("employee.read", "personnel.write")),
    }
    return tuple({
        "system_type": system, "read_operations": list(read_ops),
        "write_operations": list(write_ops), "required_scope_examples": list(scopes),
        "credential_mode": "tenant-scoped secret reference",
        "idempotency_required_for": list(write_ops),
        "live_provider_configured": False,
    } for system, (read_ops, write_ops, scopes) in profiles.items())


def _serialize(definition: WorkflowDefinition) -> dict[str, Any]:
    return {
        "workflow_id": definition.workflow_id, "tenant_id": definition.tenant_id,
        "name": definition.name, "version": definition.version,
        "trigger_type": definition.trigger_type, "status": definition.status,
        "created_by": definition.created_by,
        "nodes": [asdict(n) for n in definition.nodes],
        "edges": [asdict(e) for e in definition.edges],
    }


def create_business_automation_router(
    *, resolve_principal: Callable[[Request], Awaitable[Any]],
    repository: WorkflowDefinitionRepository,
    authorize: Callable[[Any, str, str], Awaitable[bool]],
) -> APIRouter:
    router = APIRouter(tags=["business-automation"])

    @router.get("/api/platform/v1/business-automation/connector-capabilities")
    async def get_connector_capabilities(request: Request):
        principal = await resolve_principal(request)
        if not principal or not getattr(principal, "tenant_id", None):
            raise HTTPException(401, "authentication required")
        return {"items": connector_capability_catalog()}

    @router.post("/api/platform/v1/business-automation/definitions", status_code=201)
    async def create_definition(request: Request):
        principal = await resolve_principal(request)
        tenant_id = getattr(principal, "tenant_id", None)
        actor_id = getattr(principal, "user_id", None)
        if not tenant_id or not actor_id:
            raise HTTPException(401, "authentication required")
        body = await request.json()
        workflow_id = str(body.get("workflow_id", "")).strip()
        if not await authorize(principal, workflow_id, "workflow.create"):
            raise HTTPException(403, "workflow creation is not authorized")
        try:
            nodes = tuple(WorkflowNode(str(n["node_id"]), str(n["node_type"]), n.get("config") or {}) for n in body.get("nodes", []))
            edges = tuple(WorkflowEdge(str(e["source"]), str(e["target"]), e.get("label"), e.get("condition")) for e in body.get("edges", []))
            definition = validate_workflow_graph(
                workflow_id=workflow_id, tenant_id=tenant_id, name=str(body.get("name", "")),
                version=body.get("version", 1), trigger_type=str(body.get("trigger_type", "")),
                nodes=nodes, edges=edges, created_by=actor_id,
            )
            saved = await repository.save(definition)
        except (KeyError, TypeError, WorkflowDefinitionError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return _serialize(saved)

    @router.get("/api/platform/v1/business-automation/definitions/{workflow_id}/versions/{version}")
    async def get_definition(workflow_id: str, version: int, request: Request):
        principal = await resolve_principal(request)
        tenant_id = getattr(principal, "tenant_id", None)
        if not tenant_id:
            raise HTTPException(401, "authentication required")
        if not await authorize(principal, workflow_id, "workflow.read"):
            raise HTTPException(403, "workflow read is not authorized")
        definition = await repository.get(tenant_id, workflow_id, version)
        if definition is None:
            raise HTTPException(404, "workflow definition not found")
        return _serialize(definition)

    return router
