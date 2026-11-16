import asyncio
import unittest

from agent_platform.events.consumer import RedisStreamsConsumerWorker


class FakeTransport:
    def __init__(self):
        self.connected = False
        self.closed = False
        self.groups = []

    async def connect(self):
        self.connected = True

    async def ensure_group(self, **kwargs):
        self.groups.append(kwargs)

    async def close(self):
        self.closed = True
        self.connected = False


class FakeConsumer:
    def __init__(self):
        self.transport = FakeTransport()
        self.tenant_id = "tenant-a"
        self.topic = "memory.extraction.requested"
        self.group = "memory-extraction"
        self.consumer_name = "worker-a"
        self.calls = []

    async def consume_batch(self, **kwargs):
        self.calls.append(kwargs)


class RedisStreamsConsumerWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_connects_creates_group_polls_and_closes(self):
        consumer = FakeConsumer()
        worker = RedisStreamsConsumerWorker(
            consumer=consumer, interval_seconds=0.1, batch_size=7,
            block_ms=0, pending_idle_ms=10,
        )
        await worker.start()
        await worker.start()
        await asyncio.sleep(0.02)
        self.assertTrue(worker.running)
        self.assertTrue(consumer.transport.connected)
        self.assertEqual(len(consumer.transport.groups), 1)
        self.assertTrue(consumer.calls)
        self.assertEqual(consumer.calls[0]["count"], 7)
        await worker.stop()
        self.assertFalse(worker.running)
        self.assertTrue(consumer.transport.closed)

    async def test_worker_rejects_unsafe_poll_configuration(self):
        with self.assertRaises(ValueError):
            RedisStreamsConsumerWorker(
                consumer=FakeConsumer(), interval_seconds=0,
            )
        with self.assertRaises(ValueError):
            RedisStreamsConsumerWorker(
                consumer=FakeConsumer(), batch_size=0,
            )

    async def test_group_creation_failure_closes_transport(self):
        consumer = FakeConsumer()

        async def fail_group(**kwargs):
            raise RuntimeError("group unavailable")

        consumer.transport.ensure_group = fail_group
        worker = RedisStreamsConsumerWorker(consumer=consumer)
        with self.assertRaises(RuntimeError):
            await worker.start()
        self.assertTrue(consumer.transport.closed)
        self.assertFalse(consumer.transport.connected)


if __name__ == "__main__":
    unittest.main()
