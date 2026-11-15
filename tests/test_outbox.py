import unittest

from agent_platform.events.outbox import OutboxRecord, OutboxRelay


class FakeStore:
    def __init__(self):
        self.record = OutboxRecord(
            tenant_id="tenant-a",
            outbox_id=1,
            event_id="event-1",
            topic="blackboard.entry_proposed",
            payload={"entry_id": "entry-1"},
            lease_token="00000000-0000-0000-0000-000000000001",
        )
        self.published = []
        self.failed = []

    async def claim_batch(self, *, limit):
        return [self.record][:limit]

    async def mark_published(self, record):
        self.published.append(record.event_id)
        return True

    async def mark_failed(self, record, error):
        self.failed.append((record.event_id, error))


class FakeTransport:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    async def publish(self, **kwargs):
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.messages.append(kwargs)
        return "1-0"


class OutboxRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_publishes_and_marks_outbox_record(self):
        store = FakeStore()
        transport = FakeTransport()
        result = await OutboxRelay(store=store, transport=transport).publish_pending()
        self.assertEqual(result, {"claimed": 1, "published": 1, "failed": 0})
        self.assertEqual(store.published, ["event-1"])
        self.assertEqual(transport.messages[0]["event_id"], "event-1")

    async def test_failed_publish_releases_record_for_retry(self):
        store = FakeStore()
        transport = FakeTransport(fail=True)
        result = await OutboxRelay(store=store, transport=transport).publish_pending()
        self.assertEqual(result, {"claimed": 1, "published": 0, "failed": 1})
        self.assertEqual(store.failed[0][0], "event-1")


if __name__ == "__main__":
    unittest.main()
