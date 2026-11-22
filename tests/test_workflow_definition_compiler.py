"""Tests for the declarative Workflow-to-LangGraph compiler contract."""
import unittest

from agent_platform.workflows.definition_compiler import (
    LangGraphWorkflowCompiler,
    WorkflowDefinitionError,
    WorkflowNodeRegistry,
    WorkflowNodeType,
)


def node_factory(config):
    return lambda state: {**state, **config}


class WorkflowCompilerTests(unittest.TestCase):
    def setUp(self):
        self.spec = WorkflowNodeType(
            node_type="agent", version=1, factory=node_factory,
            config_schema={"type": "object"}, input_schema={"type": "object"},
            output_schema={"type": "object"},
            ports={"inputs": ["default"], "outputs": ["default"]},
            permissions=("agent.execute",), risk="medium",
            error_retry={"max_attempts": 2},
        )
        self.compiler = LangGraphWorkflowCompiler(
            node_registry=WorkflowNodeRegistry([self.spec]),
            graph_factory=lambda definition, nodes: {
                "nodes": tuple(nodes), "definition": definition,
            },
        )
        self.definition = {
            "schema_version": 1,
            "entry_point": "first",
            "nodes": [{
                "id": "first", "node_type": "agent", "version": 1,
                "config": {"label": "first"},
                "config_schema": {"type": "object"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "ports": {"inputs": ["default"], "outputs": ["default"]},
                "permissions": ["agent.execute"], "risk": "medium",
                "error_retry": {"max_attempts": 2},
            }],
            "edges": [{"source": "first", "target": "__end__"}],
            "branches": [],
        }

    def test_valid_definition_compiles_to_registered_factories(self):
        graph, metadata = self.compiler.compile(self.definition)
        self.assertEqual(graph["nodes"], ("first",))
        self.assertEqual(metadata["engine"], "langgraph")
        self.assertFalse(metadata["execution_started"])
        self.assertEqual(len(metadata["definition_sha256"]), 64)

    def test_unknown_node_is_diagnosed_and_blocks_compile(self):
        self.definition["nodes"][0]["node_type"] = "untrusted.python"
        diagnostics = self.compiler.diagnose(self.definition)
        self.assertIn("node.unknown", {item.code for item in diagnostics})
        with self.assertRaises(WorkflowDefinitionError):
            self.compiler.compile(self.definition)

    def test_incompatible_schema_is_diagnosed(self):
        self.definition["nodes"][0]["output_schema"] = {"type": "string"}
        self.assertIn(
            "node.schema_incompatible",
            {item.code for item in self.compiler.diagnose(self.definition)},
        )

    def test_duplicate_ids_and_dangling_edges_are_rejected(self):
        duplicate = dict(self.definition["nodes"][0], id="second")
        self.definition["nodes"].append(duplicate)
        self.definition["edges"][0]["target"] = "missing"
        codes = {item.code for item in self.compiler.diagnose(self.definition)}
        self.assertIn("node.duplicate", codes)
        self.assertIn("edge.reference", codes)

    def test_invalid_entry_point_is_rejected(self):
        self.definition["entry_point"] = "missing"
        self.assertIn("entry.invalid", {item.code for item in self.compiler.diagnose(self.definition)})

    def test_branch_routes_are_validated(self):
        self.definition["branches"] = [{
            "source": "first", "field": "decision",
            "routes": {"yes": "__end__", "no": "missing"},
        }]
        self.assertIn("branch.target", {item.code for item in self.compiler.diagnose(self.definition)})

    def test_permission_metadata_cannot_be_escalated(self):
        self.definition["nodes"][0]["permissions"] = ["admin.*"]
        self.assertIn("node.permissions", {item.code for item in self.compiler.diagnose(self.definition)})

    def test_current_visual_designer_payload_normalizes_and_compiles(self):
        structural_retry = {"max_attempts": 1, "backoff": "none"}
        nodes = [
            {"id": "start", "node_type": "start", "version": 1, "config": {},
             "config_schema": {"type": "object"}, "input_schema": {"type": "object"},
             "output_schema": {"type": "object"}, "ports": {"inputs": [], "outputs": ["main"]},
             "permissions": [], "risk": "low", "error_retry": structural_retry},
            {"id": "agent", "node_type": "agent", "version": 1, "config": {},
             "config_schema": {"type": "object"}, "input_schema": {"type": "object"},
             "output_schema": {"type": "object"}, "ports": {"inputs": ["main"], "outputs": ["main"]},
             "permissions": ["agent.execute"], "risk": "medium",
             "error_retry": {"max_attempts": 2}},
            {"id": "end", "node_type": "end", "version": 1, "config": {},
             "config_schema": {"type": "object"}, "input_schema": {"type": "object"},
             "output_schema": {"type": "object"}, "ports": {"inputs": ["main"], "outputs": []},
             "permissions": [], "risk": "low", "error_retry": structural_retry},
        ]
        designer = {
            "name": "Flow", "description": "designer graph", "nodes": nodes,
            "edges": [
                {"id": "e1", "source": "start", "target": "agent", "source_port": "main", "target_port": "main"},
                {"id": "e2", "source": "agent", "target": "end", "source_port": "main", "target_port": "main"},
            ],
        }
        designer_spec = WorkflowNodeType(
            node_type="agent", version=1, factory=node_factory,
            config_schema={"type": "object"}, input_schema={"type": "object"},
            output_schema={"type": "object"},
            ports={"inputs": ["main"], "outputs": ["main"]},
            permissions=("agent.execute",), risk="medium",
            error_retry={"max_attempts": 2},
        )
        compiler = LangGraphWorkflowCompiler(
            node_registry=WorkflowNodeRegistry([designer_spec]),
            graph_factory=lambda definition, compiled: {"entries": definition["entry_points"], "nodes": tuple(compiled)},
        )
        graph, metadata = compiler.compile(designer)
        self.assertEqual(graph["entries"], ["agent"])
        self.assertEqual(graph["nodes"], ("agent",))
        self.assertEqual(metadata["engine"], "langgraph")

    def test_visual_designer_payload_builds_real_langgraph(self):
        designer = {
            "nodes": [
                {"id": "start", "node_type": "start", "version": 1,
                 "ports": {"inputs": [], "outputs": ["main"]}},
                {"id": "agent", "node_type": "agent", "version": 1, "config": {},
                 "config_schema": {"type": "object"}, "input_schema": {"type": "object"},
                 "output_schema": {"type": "object"}, "ports": {"inputs": ["main"], "outputs": ["main"]},
                 "permissions": ["agent.execute"], "risk": "medium",
                 "error_retry": {"max_attempts": 2}},
                {"id": "end", "node_type": "end", "version": 1,
                 "ports": {"inputs": ["main"], "outputs": []}},
            ],
            "edges": [
                {"source": "start", "target": "agent", "source_port": "main", "target_port": "main"},
                {"source": "agent", "target": "end", "source_port": "main", "target_port": "main"},
            ],
        }
        designer_spec = WorkflowNodeType(
            node_type="agent", version=1, factory=lambda config: lambda state: state,
            config_schema={"type": "object"}, input_schema={"type": "object"},
            output_schema={"type": "object"},
            ports={"inputs": ["main"], "outputs": ["main"]},
            permissions=("agent.execute",), risk="medium",
            error_retry={"max_attempts": 2},
        )
        graph, metadata = LangGraphWorkflowCompiler(
            node_registry=WorkflowNodeRegistry([designer_spec]),
        ).compile(designer)
        self.assertTrue(callable(getattr(graph, "invoke", None)))
        self.assertEqual(metadata["edge_count"], 2)
        self.assertFalse(metadata["execution_started"])

    def test_invalid_retry_attempts_are_rejected(self):
        self.definition["nodes"][0]["error_retry"] = {"max_attempts": 0}
        self.assertIn("node.retry", {item.code for item in self.compiler.diagnose(self.definition)})

    def test_real_langgraph_stategraph_compiles_without_starting_a_run(self):
        spec = WorkflowNodeType(
            node_type="agent", version=1, factory=lambda config: lambda state: {"done": True},
            config_schema={"type": "object"}, input_schema={"type": "object"},
            output_schema={"type": "object"},
            ports={"inputs": ["default"], "outputs": ["default"]},
        )
        compiler = LangGraphWorkflowCompiler(node_registry=WorkflowNodeRegistry([spec]))
        graph, metadata = compiler.compile(self.definition)
        self.assertTrue(callable(getattr(graph, "invoke", None)))
        self.assertFalse(metadata["execution_started"])

    def test_graph_compile_exception_is_returned_as_diagnostic(self):
        compiler = LangGraphWorkflowCompiler(
            node_registry=WorkflowNodeRegistry([self.spec]),
            graph_factory=lambda definition, nodes: (_ for _ in ()).throw(RuntimeError("compile exploded")),
        )
        with self.assertRaises(WorkflowDefinitionError) as raised:
            compiler.compile(self.definition)
        self.assertEqual(raised.exception.diagnostics[0].code, "compile.failed")


if __name__ == "__main__":
    unittest.main()
