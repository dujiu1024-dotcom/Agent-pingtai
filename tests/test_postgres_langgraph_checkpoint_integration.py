"""Durable LangGraph pause/resume tests against the CI PostgreSQL service.

These tests deliberately use the real PostgreSQL checkpointer. They verify that
an approval interrupt survives closing the original checkpointer/session and can
be resumed by a newly constructed checkpointer, rather than relying on
InMemorySaver. Business integrations remain covered by their owning suites.
"""
from __future__ import annotations

import os
import unittest
from typing import TypedDict
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class PostgresLangGraphCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_human_approval_interrupt_survives_checkpointer_restart(self):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Command, interrupt

        class WorkflowState(TypedDict):
            approved: bool
            artifact_id: str
            delivery_status: str

        def request_approval(state: WorkflowState) -> dict[str, bool]:
            decision = interrupt({
                "action": "deliver_artifact",
                "artifact_id": state["artifact_id"],
            })
            return {"approved": decision is True}

        def deliver_artifact(state: WorkflowState) -> dict[str, str]:
            return {
                "delivery_status": "delivered" if state["approved"] else "rejected"
            }

        builder = StateGraph(WorkflowState)
        builder.add_node("request_approval", request_approval)
        builder.add_node("deliver_artifact", deliver_artifact)
        builder.add_edge(START, "request_approval")
        builder.add_edge("request_approval", "deliver_artifact")
        builder.add_edge("deliver_artifact", END)

        async def exercise_decision(decision: bool) -> None:
            thread_id = f"postgres-checkpoint-{uuid4()}"
            config = {"configurable": {"thread_id": thread_id}}
            async with AsyncPostgresSaver.from_conn_string(DSN) as checkpointer:
                await checkpointer.setup()
                graph = builder.compile(checkpointer=checkpointer)
                paused = await graph.ainvoke(
                    {
                        "approved": False,
                        "artifact_id": f"artifact-{thread_id}",
                        "delivery_status": "pending",
                    },
                    config=config,
                )
                self.assertIn("__interrupt__", paused)
                interrupt_value = paused["__interrupt__"][0].value
                self.assertEqual(interrupt_value["action"], "deliver_artifact")
                self.assertEqual(
                    interrupt_value["artifact_id"], f"artifact-{thread_id}"
                )

            # A new saver instance simulates a worker/process restart. The resume
            # must load the checkpoint from PostgreSQL, not the prior Python object.
            async with AsyncPostgresSaver.from_conn_string(DSN) as restarted_saver:
                await restarted_saver.setup()
                restarted_graph = builder.compile(checkpointer=restarted_saver)
                completed = await restarted_graph.ainvoke(
                    Command(resume=decision), config=config
                )

            self.assertEqual(
                completed["delivery_status"],
                "delivered" if decision else "rejected",
            )

        await exercise_decision(True)
        await exercise_decision(False)


if __name__ == "__main__":
    unittest.main()
