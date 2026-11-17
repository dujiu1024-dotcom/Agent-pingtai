from __future__ import annotations

import unittest
from datetime import timedelta

from agent_platform.blackboard.event_delivery import BlackboardEventDeliveryStore


class BlackboardEventDeliveryTests(unittest.TestCase):
    def test_retry_delay_is_capped_exponential(self):
        self.assertEqual(
            BlackboardEventDeliveryStore.retry_delay(1), timedelta(seconds=2)
        )
        self.assertEqual(
            BlackboardEventDeliveryStore.retry_delay(4), timedelta(seconds=16)
        )
        self.assertEqual(
            BlackboardEventDeliveryStore.retry_delay(20), timedelta(seconds=300)
        )

    def test_retry_delay_rejects_invalid_bounds(self):
        for args in ((0, 2, 10), (1, 0, 10), (1, 10, 2)):
            with self.assertRaises(ValueError):
                BlackboardEventDeliveryStore.retry_delay(
                    args[0], base_seconds=args[1], max_seconds=args[2]
                )

    def test_store_requires_safe_configuration(self):
        with self.assertRaises(ValueError):
            BlackboardEventDeliveryStore(tenant_id="", connection_factory=lambda: None)
        with self.assertRaises(ValueError):
            BlackboardEventDeliveryStore(
                tenant_id="t", connection_factory=lambda: None, lease_seconds=0
            )
        with self.assertRaises(ValueError):
            BlackboardEventDeliveryStore(
                tenant_id="t", connection_factory=lambda: None, max_attempts=0
            )


if __name__ == "__main__":
    unittest.main()
