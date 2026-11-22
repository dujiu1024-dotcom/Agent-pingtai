"""Compile versioned declarative Workflow definitions into LangGraph StateGraphs.

Only platform-registered node factories are executable. User definitions never
supply Python import paths, callables, scheduler logic, or checkpointer objects.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class WorkflowNodeType:
    node_type: str
    version: int
    factory: Callable[[Mapping[str, Any]], Callable[..., Any]]
    config_schema: Mapping[str, Any]
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    ports: Mapping[str, Any]
    permissions: tuple[str, ...] = ()
    risk: str = "low"
    error_retry: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class WorkflowDiagnostic:
    code: str
    message: str
    path: str
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code, "message": self.message,
            "path": self.path, "severity": self.severity,
        }


class WorkflowDefinitionError(ValueError):
    def __init__(self, diagnostics: Sequence[WorkflowDiagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        super().__init__("; ".join(item.message for item in self.diagnostics))


class WorkflowNodeRegistry:
    """Explicit allowlist of reviewed platform node implementations."""

    def __init__(self, node_types: Sequence[WorkflowNodeType] = ()) -> None:
        self._types: dict[tuple[str, int], WorkflowNodeType] = {}
        for item in node_types:
            self.register(item)

    def register(self, item: WorkflowNodeType) -> None:
        key = (item.node_type, item.version)
        if not item.node_type.strip() or item.version < 1 or key in self._types:
            raise ValueError("node type/version must be unique and valid")
        self._types[key] = item

    def resolve(self, node_type: str, version: int) -> WorkflowNodeType | None:
        return self._types.get((node_type, version))


class LangGraphWorkflowCompiler:
    """Validate and compile immutable Workflow versions; never executes a graph."""

    def __init__(
        self, *, node_registry: WorkflowNodeRegistry,
        graph_factory: Callable[[Mapping[str, Any], Mapping[str, Callable[..., Any]]], Any] | None = None,
    ) -> None:
        self.node_registry = node_registry
        self._graph_factory = graph_factory or self._compile_state_graph

    @staticmethod
    def _compile_state_graph(
        definition: Mapping[str, Any], nodes: Mapping[str, Callable[..., Any]],
    ) -> Any:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise RuntimeError("LangGraph is required to compile Workflow definitions") from exc

        from langgraph.types import RetryPolicy

        graph: Any = StateGraph(dict)
        for node in definition["nodes"]:
            node_id = node["id"]
            if node.get("node_type") in {"start", "end"}:
                continue
            node_callable = nodes[node_id]
            retry = node.get("error_retry", {})
            attempts = retry.get("max_attempts", 1) if isinstance(retry, Mapping) else 1
            backoff = retry.get("backoff", "exponential") if isinstance(retry, Mapping) else "exponential"
            if attempts > 1:
                graph.add_node(
                    node_id, node_callable,
                    retry_policy=RetryPolicy(
                        max_attempts=attempts,
                        initial_interval=0.001 if backoff == "none" else 0.5,
                        backoff_factor=1.0 if backoff == "linear" else 2.0,
                    ),
                )
            else:
                graph.add_node(node_id, node_callable)

        for entry in definition["entry_points"]:
            graph.add_edge(START, entry)
        conditional_sources: set[str] = set()
        for edge in definition.get("edges", []):
            source, target = edge["source"], edge["target"]
            if source in conditional_sources:
                raise ValueError(f"node {source} cannot mix normal and conditional outgoing edges")
            graph.add_edge(source, END if target == "__end__" else target)

        for branch in definition.get("branches", []):
            source = branch["source"]
            if source in conditional_sources:
                raise ValueError(f"node {source} has multiple conditional branch declarations")
            conditional_sources.add(source)
            field = branch["field"]
            routes = dict(branch["routes"])
            default = branch["default"]
            def route(state: Mapping[str, Any], *, key: str = field, fallback: Any = default, route_map: Mapping[str, Any] = routes) -> Any:
                value = state.get(key)
                return value if isinstance(value, str) and value in route_map else fallback
            graph.add_conditional_edges(
                source, route,
                {key: END if target == "__end__" else target for key, target in routes.items()},
            )
        return graph.compile()

    @staticmethod
    def normalize_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
        """Accept the current designer payload and normalize it to compiler schema v1."""
        value = dict(definition)
        nodes = value.get("nodes")
        edges = value.get("edges", [])
        if value.get("schema_version") == 1 and "entry_point" in value:
            value["entry_points"] = [value.pop("entry_point")]
            return value
        if not isinstance(nodes, list) or not isinstance(edges, list):
            return value
        starts = [node for node in nodes if isinstance(node, Mapping) and node.get("node_type") == "start"]
        if len(starts) != 1:
            return value
        start_id = starts[0].get("id")
        start_edges = [edge for edge in edges if isinstance(edge, Mapping) and edge.get("source") == start_id]
        if not start_edges:
            return value
        normalized_edges: list[dict[str, Any]] = []
        port_checks: list[dict[str, Any]] = []
        branches: dict[str, dict[str, Any]] = {}
        node_map = {
            node.get("id"): node for node in nodes
            if isinstance(node, Mapping) and isinstance(node.get("id"), str)
        }
        for edge in edges:
            if not isinstance(edge, Mapping):
                normalized_edges.append(edge)
                continue
            source, target = edge.get("source"), edge.get("target")
            port_checks.append({
                "source": source, "target": target,
                "source_port": edge.get("source_port", "default"),
                "target_port": edge.get("target_port", "default"),
            })
            if source == start_id:
                continue
            source_node = node_map.get(source) if isinstance(source, str) else None
            target_node = node_map.get(target) if isinstance(target, str) else None
            if isinstance(source_node, Mapping) and source_node.get("node_type") == "condition" and isinstance(target, str):
                raw_config = source_node.get("config", {})
                config = raw_config if isinstance(raw_config, Mapping) else {}
                branch = branches.setdefault(source, {
                    "source": source,
                    "field": str(config.get("route_field", "route")),
                    "routes": {},
                    "default": str(config.get("default_port") or edge.get("source_port") or ""),
                })
                route_key = str(edge.get("source_port") or "default")
                if route_key in branch["routes"]:
                    branch["routes"][route_key] = "__duplicate_route__"
                else:
                    branch["routes"][route_key] = (
                        "__end__" if isinstance(target_node, Mapping) and target_node.get("node_type") == "end"
                        else target
                    )
                continue
            normalized_edges.append({
                "source": source,
                "target": "__end__" if isinstance(target_node, Mapping) and target_node.get("node_type") == "end" else target,
            })
        return {
            "schema_version": 1,
            "entry_points": [
                edge.get("target") for edge in start_edges
                if isinstance(edge.get("target"), str)
                and edge.get("target") in node_map
                and node_map[edge.get("target")].get("node_type") not in {"start", "end"}
            ],
            "nodes": nodes,
            "edges": normalized_edges,
            "branches": list(branches.values()),
            "port_checks": port_checks,
        }

    def diagnose(self, definition: Mapping[str, Any]) -> tuple[WorkflowDiagnostic, ...]:
        if isinstance(definition, Mapping):
            definition = self.normalize_definition(definition)
        diagnostics: list[WorkflowDiagnostic] = []
        if not isinstance(definition, Mapping):
            return (WorkflowDiagnostic("definition.type", "definition must be an object", "$"),)
        if definition.get("schema_version") != 1:
            diagnostics.append(WorkflowDiagnostic(
                "schema.unsupported", "schema_version must equal 1 or provide one start node in the designer payload", "$.schema_version",
            ))
        nodes = definition.get("nodes")
        edges = definition.get("edges", [])
        branches = definition.get("branches", [])
        if not isinstance(nodes, list) or not nodes:
            diagnostics.append(WorkflowDiagnostic("nodes.required", "nodes must be a non-empty array", "$.nodes"))
            nodes = []
        if not isinstance(edges, list):
            diagnostics.append(WorkflowDiagnostic("edges.type", "edges must be an array", "$.edges"))
            edges = []
        if not isinstance(branches, list):
            diagnostics.append(WorkflowDiagnostic("branches.type", "branches must be an array", "$.branches"))
            branches = []

        ids: set[str] = set()
        node_by_id: dict[str, Mapping[str, Any]] = {}
        for index, node in enumerate(nodes):
            path = f"$.nodes[{index}]"
            if not isinstance(node, Mapping):
                diagnostics.append(WorkflowDiagnostic("node.type", "node must be an object", path))
                continue
            node_id, kind, version = node.get("id"), node.get("node_type"), node.get("version")
            if not isinstance(node_id, str) or not node_id.strip():
                diagnostics.append(WorkflowDiagnostic("node.id", "node id is required", path + ".id"))
                continue
            if node_id in ids:
                diagnostics.append(WorkflowDiagnostic("node.duplicate", f"duplicate node id: {node_id}", path + ".id"))
            ids.add(node_id)
            node_by_id[node_id] = node
            if not isinstance(kind, str) or isinstance(version, bool) or not isinstance(version, int):
                diagnostics.append(WorkflowDiagnostic("node.type_version", "node_type and integer version are required", path))
                continue
            if kind in {"start", "end"}:
                if version != 1:
                    diagnostics.append(WorkflowDiagnostic("node.structural_version", "start/end nodes support version 1 only", path + ".version"))
                if not isinstance(node.get("ports"), Mapping):
                    diagnostics.append(WorkflowDiagnostic("node.ports", "structural nodes require ports metadata", path + ".ports"))
                continue
            spec = self.node_registry.resolve(kind, version)
            if spec is None:
                diagnostics.append(WorkflowDiagnostic(
                    "node.unknown", f"unknown or incompatible node type/version: {kind}@{version}",
                    path + ".node_type",
                ))
                continue
            for key, expected in (
                ("config_schema", spec.config_schema), ("input_schema", spec.input_schema),
                ("output_schema", spec.output_schema), ("ports", spec.ports),
            ):
                if node.get(key) != expected:
                    diagnostics.append(WorkflowDiagnostic(
                        "node.schema_incompatible", f"{key} does not match registered {kind}@{version}",
                        path + "." + key,
                    ))
            if node.get("permissions", []) != list(spec.permissions):
                diagnostics.append(WorkflowDiagnostic(
                    "node.permissions", "permissions differ from registered node policy",
                    path + ".permissions",
                ))
            if node.get("risk", spec.risk) != spec.risk:
                diagnostics.append(WorkflowDiagnostic("node.risk", "risk metadata differs from registry", path + ".risk"))
            if node.get("error_retry", dict(spec.error_retry or {})) != dict(spec.error_retry or {}):
                diagnostics.append(WorkflowDiagnostic(
                    "node.retry", "error_retry semantics differ from registry", path + ".error_retry",
                ))
            config = node.get("config", {})
            if not isinstance(config, Mapping):
                diagnostics.append(WorkflowDiagnostic("node.config", "config must be an object", path + ".config"))
            else:
                try:
                    from jsonschema import Draft202012Validator
                    Draft202012Validator.check_schema(spec.config_schema)
                    for error in Draft202012Validator(spec.config_schema).iter_errors(config):
                        diagnostics.append(WorkflowDiagnostic(
                            "node.config_invalid", error.message[:300],
                            path + ".config" + "".join(f"[{part}]" for part in error.absolute_path),
                        ))
                    Draft202012Validator.check_schema(spec.input_schema)
                    Draft202012Validator.check_schema(spec.output_schema)
                except ImportError:
                    diagnostics.append(WorkflowDiagnostic(
                        "schema.validator_unavailable", "jsonschema is required for Workflow schema validation",
                        path + ".config_schema",
                    ))
                except Exception as exc:
                    diagnostics.append(WorkflowDiagnostic(
                        "node.schema_invalid", str(exc)[:300], path + ".config_schema",
                    ))
            retry = node.get("error_retry", {})
            if not isinstance(retry, Mapping):
                diagnostics.append(WorkflowDiagnostic("node.retry", "error_retry must be an object", path + ".error_retry"))
            else:
                if "max_attempts" in retry and (
                    isinstance(retry["max_attempts"], bool)
                    or not isinstance(retry["max_attempts"], int)
                    or retry["max_attempts"] < 1
                ):
                    diagnostics.append(WorkflowDiagnostic(
                        "node.retry", "max_attempts must be a positive integer", path + ".error_retry.max_attempts",
                    ))
                if retry.get("backoff", "exponential") not in {"none", "linear", "exponential"}:
                    diagnostics.append(WorkflowDiagnostic(
                        "node.retry", "backoff must be none, linear, or exponential", path + ".error_retry.backoff",
                    ))

        entries = definition.get("entry_points")
        if not isinstance(entries, list) or not entries or any(
            not isinstance(entry, str) or entry not in ids
            or node_by_id.get(entry, {}).get("node_type") in {"start", "end"}
            for entry in entries
        ):
            diagnostics.append(WorkflowDiagnostic("entry.invalid", "entry_points must reference executable known nodes", "$.entry_points"))
        if any(isinstance(node, Mapping) and node.get("node_type") in {"start", "end"} for node in nodes):
            start_count = sum(1 for node in nodes if isinstance(node, Mapping) and node.get("node_type") == "start")
            end_count = sum(1 for node in nodes if isinstance(node, Mapping) and node.get("node_type") == "end")
            if start_count != 1:
                diagnostics.append(WorkflowDiagnostic("start.count", "designer graph requires exactly one start node", "$.nodes"))
            if end_count < 1:
                diagnostics.append(WorkflowDiagnostic("end.required", "designer graph requires at least one end node", "$.nodes"))

        seen_edges: set[tuple[str, str]] = set()
        for index, edge in enumerate(edges):
            path = f"$.edges[{index}]"
            if not isinstance(edge, Mapping):
                diagnostics.append(WorkflowDiagnostic("edge.type", "edge must be an object", path))
                continue
            source, target = edge.get("source"), edge.get("target")
            if not isinstance(source, str) or source not in ids or not isinstance(target, str) or target not in ids | {"__end__"}:
                diagnostics.append(WorkflowDiagnostic("edge.reference", "edge references an unknown node", path))
            elif node_by_id.get(source, {}).get("node_type") == "end" or node_by_id.get(target, {}).get("node_type") == "start":
                diagnostics.append(WorkflowDiagnostic("edge.direction", "edges cannot leave end or enter start", path))
            else:
                pass
            if isinstance(source, str) and isinstance(target, str):
                if (source, target) in seen_edges:
                    diagnostics.append(WorkflowDiagnostic("edge.duplicate", "duplicate edge", path))
                seen_edges.add((source, target))

        for index, edge in enumerate(definition.get("port_checks", [])):
            if not isinstance(edge, Mapping):
                continue
            source, target = edge.get("source"), edge.get("target")
            if not isinstance(source, str) or not isinstance(target, str):
                continue
            source_ports_meta = node_by_id.get(source, {}).get("ports")
            target_ports_meta = node_by_id.get(target, {}).get("ports")
            source_ports = source_ports_meta.get("outputs", []) if isinstance(source_ports_meta, Mapping) else []
            target_ports = target_ports_meta.get("inputs", []) if isinstance(target_ports_meta, Mapping) else []
            if edge.get("source_port") not in source_ports:
                diagnostics.append(WorkflowDiagnostic("edge.source_port", "edge source port is not declared", f"$.edges[{index}].source_port"))
            if edge.get("target_port") not in target_ports:
                diagnostics.append(WorkflowDiagnostic("edge.target_port", "edge target port is not declared", f"$.edges[{index}].target_port"))

        branch_sources: set[str] = set()
        for index, branch in enumerate(branches):
            path = f"$.branches[{index}]"
            if not isinstance(branch, Mapping):
                diagnostics.append(WorkflowDiagnostic("branch.type", "branch must be an object", path))
                continue
            source, field, routes = branch.get("source"), branch.get("field"), branch.get("routes")
            if not isinstance(source, str) or source not in ids or not isinstance(field, str) or not field:
                diagnostics.append(WorkflowDiagnostic("branch.source", "branch source and state field are required", path))
            if isinstance(source, str) and source in branch_sources:
                diagnostics.append(WorkflowDiagnostic("branch.duplicate", "only one branch declaration per source is allowed", path))
            if isinstance(source, str):
                branch_sources.add(source)
            if not isinstance(routes, Mapping) or not routes:
                diagnostics.append(WorkflowDiagnostic("branch.routes", "routes must be a non-empty object", path + ".routes"))
                continue
            if branch.get("default") not in routes:
                diagnostics.append(WorkflowDiagnostic("branch.default", "default must name a route key", path + ".default"))
            for route_key, target in routes.items():
                if target == "__duplicate_route__":
                    diagnostics.append(WorkflowDiagnostic("branch.duplicate_port", "condition output port is connected more than once", path + ".routes"))
                if target != "__duplicate_route__" and (not isinstance(target, str) or target not in ids | {"__end__"}):
                    diagnostics.append(WorkflowDiagnostic("branch.target", "branch target is unknown", path + ".routes"))
        for edge in edges:
            if isinstance(edge, Mapping) and isinstance(edge.get("source"), str) and edge.get("source") in branch_sources:
                diagnostics.append(WorkflowDiagnostic(
                    "graph.mixed_outgoing", "a node cannot mix normal edges and conditional branches",
                    "$.edges",
                ))
        return tuple(diagnostics)

    def compile(self, definition: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
        definition = self.normalize_definition(definition)
        diagnostics = self.diagnose(definition)
        if diagnostics:
            raise WorkflowDefinitionError(diagnostics)
        compiled_nodes: dict[str, Callable[..., Any]] = {}
        for node in definition["nodes"]:
            if node.get("node_type") in {"start", "end"}:
                continue
            spec = self.node_registry.resolve(node["node_type"], node["version"])
            if spec is None:  # guarded by diagnose; protects against mutable registries
                raise WorkflowDefinitionError((WorkflowDiagnostic(
                    "node.unknown", "node type disappeared during compilation",
                    "$.nodes",
                ),))
            try:
                compiled_nodes[node["id"]] = spec.factory(dict(node.get("config", {})))
            except Exception as exc:
                raise WorkflowDefinitionError((WorkflowDiagnostic(
                    "node.factory_failed", str(exc)[:500], f"$.nodes[{node['id']}].config",
                ),)) from exc
        try:
            graph = self._graph_factory(definition, compiled_nodes)
        except Exception as exc:
            raise WorkflowDefinitionError((WorkflowDiagnostic(
                "compile.failed", str(exc)[:500], "$",
            ),)) from exc
        canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return graph, {
            "definition_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "node_count": len(compiled_nodes),
            "edge_count": len(definition.get("port_checks", definition.get("edges", []))) + sum(
                len(branch.get("routes", {})) for branch in definition.get("branches", [])
            ),
            "engine": "langgraph",
            "execution_started": False,
        }
