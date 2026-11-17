from __future__ import annotations

import asyncio
import unittest

from agent_platform.streaming.run_events import RunEventBroker


class RunEventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_and_subscribe_replay_from_cursor(self):
        broker = RunEventBroker()
        await broker.publish(tenant_id="t", run_id="r", event_type="RUN_START")
        await broker.publish(tenant_id="t", run_id="r", event_type="TOKEN_STREAM", data={"token": "A"})
        stream = await broker.subscribe(tenant_id="t", run_id="r", last_event_id="r:1")
        event = await anext(stream)
        self.assertEqual(event.event, "token_stream")
        self.assertEqual(event.data["token"], "A")
        self.assertEqual(event.event_id, "r:2")
        await stream.aclose()

    async def test_rejects_cross_run_cursor_and_expired_cursor(self):
        broker = RunEventBroker(history_limit=2)
        with self.assertRaises(ValueError):
            await broker.subscribe(
                tenant_id="t", run_id="r", last_event_id="other:1",
            )
        for name in ("RUN_START", "STEP_START", "STEP_FINISH"):
            await broker.publish(tenant_id="t", run_id="r", event_type=name)
        with self.assertRaises(ValueError):
            await broker.subscribe(
                tenant_id="t", run_id="r", last_event_id="r:1",
            )

    async def test_slow_subscriber_is_detached_without_blocking_publish(self):
        broker = RunEventBroker(history_limit=10, subscriber_limit=1)
        stream = await broker.subscribe(tenant_id="t", run_id="r")
        first = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        await broker.publish(tenant_id="t", run_id="r", event_type="RUN_START")
        await broker.publish(tenant_id="t", run_id="r", event_type="STEP_START")
        self.assertEqual((await first).data["event_type"], "RUN_START")
        await stream.aclose()


if __name__ == "__main__":
    unittest.main()
