import sys
import types
import unittest
from unittest.mock import patch

from agent_platform.runtime.postgres_checkpointer import (
    compiled_postgres_graph,
    postgres_checkpointer,
)


class PostgresCheckpointerTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_dsn(self):
        with self.assertRaisesRegex(ValueError, "DSN"):
            async with postgres_checkpointer(" "):
                pass

    async def test_opens_saver_runs_setup_and_keeps_context_alive(self):
        calls = []

        class FakeSaver:
            async def setup(self):
                calls.append("setup")

        class SaverContext:
            async def __aenter__(self):
                calls.append("enter")
                return FakeSaver()

            async def __aexit__(self, *_):
                calls.append("exit")

        class FakeSaverType:
            @staticmethod
            def from_conn_string(dsn):
                calls.append(("dsn", dsn))
                return SaverContext()

        package = types.ModuleType("langgraph")
        checkpoint = types.ModuleType("langgraph.checkpoint")
        postgres = types.ModuleType("langgraph.checkpoint.postgres")
        aio = types.ModuleType("langgraph.checkpoint.postgres.aio")
        aio.AsyncPostgresSaver = FakeSaverType
        with patch.dict(sys.modules, {
            "langgraph": package,
            "langgraph.checkpoint": checkpoint,
            "langgraph.checkpoint.postgres": postgres,
            "langgraph.checkpoint.postgres.aio": aio,
        }):
            async with postgres_checkpointer("postgresql://db") as saver:
                self.assertIsInstance(saver, FakeSaver)
                self.assertEqual(calls, [("dsn", "postgresql://db"), "enter", "setup"])
            self.assertEqual(calls[-1], "exit")

    async def test_compiled_graph_uses_durable_saver(self):
        calls = []

        class FakeSaver:
            async def setup(self):
                calls.append("setup")

        class SaverContext:
            async def __aenter__(self):
                return FakeSaver()

            async def __aexit__(self, *_):
                calls.append("closed")

        class FakeSaverType:
            @staticmethod
            def from_conn_string(_dsn):
                return SaverContext()

        class Builder:
            def compile(self, *, checkpointer):
                calls.append(checkpointer)
                return "compiled-graph"

        modules = {
            "langgraph": types.ModuleType("langgraph"),
            "langgraph.checkpoint": types.ModuleType("langgraph.checkpoint"),
            "langgraph.checkpoint.postgres": types.ModuleType("langgraph.checkpoint.postgres"),
            "langgraph.checkpoint.postgres.aio": types.ModuleType("langgraph.checkpoint.postgres.aio"),
        }
        modules["langgraph.checkpoint.postgres.aio"].AsyncPostgresSaver = FakeSaverType
        with patch.dict(sys.modules, modules):
            async with compiled_postgres_graph(Builder(), "postgresql://db") as graph:
                self.assertEqual(graph, "compiled-graph")
                self.assertIsInstance(calls[-1], FakeSaver)
            self.assertEqual(calls[-1], "closed")


if __name__ == "__main__":
    unittest.main()
