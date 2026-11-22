"""Exhaustive Run state-transition rejection matrix for Issue #9 / L3-T26."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.runtime.lifecycle import (
    InMemoryRunStateStore,
    RunSnapshot,
    RunStatus,
)


# Keep the contract explicit: every source/target pair is classified. This
# makes adding a RunStatus fail the test until its transition semantics are
# deliberately reviewed.
_ALLOWED_TRANSITIONS = {
    RunStatus.QUEUED: {
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.RUNNING: {
        RunStatus.PAUSED,
        RunStatus.CANCELLING,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.TIMED_OUT,
        RunStatus.INTERRUPTED,
    },
    RunStatus.PAUSED: {
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    },
    RunStatus.CANCELLING: {
        RunStatus.CANCELLED,
        RunStatus.FAILED,
        RunStatus.TIMED_OUT,
    },
    RunStatus.INTERRUPTED: {
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
    RunStatus.TIMED_OUT: set(),
}


class RunTransitionMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_status_pair_obeys_the_transition_contract(self) -> None:
        statuses = tuple(RunStatus)
        self.assertEqual(
            set(_ALLOWED_TRANSITIONS),
            set(statuses),
            "the transition contract must classify every RunStatus",
        )

        checked_pairs = 0
        rejected_pairs = 0
        for source in statuses:
            for target in statuses:
                with self.subTest(source=source.value, target=target.value):
                    store = InMemoryRunStateStore()
                    initial = RunSnapshot(
                        tenant_id="tenant",
                        run_id="run",
                        status=source,
                        version=7,
                        updated_at=datetime.now(timezone.utc),
                    )
                    # Seed each source state independently so the test covers
                    # all ordered pairs, including transitions out of terminal
                    # states, without relying on a path to reach that state.
                    store._rows[("tenant", "run")] = initial
                    candidate = RunSnapshot(
                        tenant_id="tenant",
                        run_id="run",
                        status=target,
                        version=initial.version,
                    )

                    if target in _ALLOWED_TRANSITIONS[source]:
                        updated = await store.transition(
                            candidate, expected_version=initial.version
                        )
                        self.assertEqual(updated.status, target)
                        self.assertEqual(updated.version, initial.version + 1)
                    else:
                        with self.assertRaisesRegex(
                            ValueError, "illegal Run transition"
                        ):
                            await store.transition(
                                candidate, expected_version=initial.version
                            )
                        self.assertEqual(
                            await store.get("tenant", "run"),
                            initial,
                            "rejected transition must not mutate persisted state",
                        )
                        rejected_pairs += 1
                    checked_pairs += 1

        self.assertEqual(checked_pairs, len(statuses) ** 2)
        self.assertEqual(rejected_pairs, 63)


if __name__ == "__main__":
    unittest.main()
