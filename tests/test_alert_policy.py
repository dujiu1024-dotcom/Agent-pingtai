from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.observability.alert_policy import (
    AlertInhibitionRule, AlertPolicyEngine, AlertRetryPolicy, OnCallRotation,
)
from agent_platform.observability.alerts import AlertEvent


class AlertPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 8, tzinfo=timezone.utc)

    def event(self, rule, metric="queue_depth", severity="warning", offset=0):
        return AlertEvent(
            rule_id=rule, metric_name=metric, observed_value=20, threshold=10,
            severity=severity, fired_at=self.now + timedelta(seconds=offset),
            description=rule,
        )

    def test_inhibition_only_suppresses_targets_when_source_severity_is_met(self):
        policy = AlertPolicyEngine(inhibition_rules=(
            AlertInhibitionRule("cluster-down", frozenset({"queue-lag", "worker-lag"}), "critical"),
        ))
        candidates = (
            self.event("cluster-down", severity="warning"),
            self.event("queue-lag"),
        )
        self.assertEqual(policy.filter_inhibited(candidates), candidates)
        critical = (self.event("cluster-down", severity="critical"), self.event("queue-lag"))
        self.assertEqual([item.rule_id for item in policy.filter_inhibited(critical)], ["cluster-down"])

    def test_aggregation_groups_only_same_metric_severity_within_window(self):
        policy = AlertPolicyEngine(aggregation_window=timedelta(seconds=30), max_digest_events=2)
        events = (
            self.event("a", offset=0),
            self.event("b", offset=10),
            self.event("c", offset=20),
            self.event("d", severity="critical", offset=5),
            self.event("e", offset=60),
        )
        digests = policy.aggregate(events)
        self.assertEqual([len(d.events) for d in digests], [2, 1, 1, 1])
        self.assertEqual(digests[0].metric_name, "queue_depth")
        self.assertEqual(digests[0].events[0].rule_id, "a")

    def test_on_call_rotation_and_ack_deadline_are_deterministic(self):
        rotation = OnCallRotation(
            user_ids=("alice", "bob"),
            starts_at=self.now,
            rotation_interval=timedelta(hours=1),
            acknowledgement_timeout=timedelta(minutes=10),
        )
        first = rotation.assign(self.now + timedelta(minutes=5))
        second = rotation.assign(self.now + timedelta(hours=1))
        self.assertEqual(first.user_id, "alice")
        self.assertEqual(first.acknowledged_by_deadline, self.now + timedelta(minutes=15))
        self.assertEqual(second.user_id, "bob")

    def test_retry_policy_is_bounded_and_marks_exhaustion(self):
        policy = AlertRetryPolicy(max_attempts=4, initial_delay=timedelta(seconds=5),
                                  maximum_delay=timedelta(seconds=12))
        self.assertEqual([policy.delay_after(n).total_seconds() for n in (1, 2, 3, 4)],
                         [5, 10, 12, 12])
        self.assertFalse(policy.exhausted(3))
        self.assertTrue(policy.exhausted(4))


if __name__ == "__main__":
    unittest.main()
