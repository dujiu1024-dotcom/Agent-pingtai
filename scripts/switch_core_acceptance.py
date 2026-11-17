#!/usr/bin/env python3
"""Run the core platform journeys and fail on test failures or error-level logs."""
from __future__ import annotations

import logging
import sys
import unittest
from dataclasses import dataclass


CORE_SUITES = (
    "tests.test_core_business_journey_e2e",
    "tests.test_platform_e2e_release_gate",
    "tests.test_run_lifecycle",
    "tests.test_event_consumer",
    "tests.test_outbox_worker",
    "tests.test_alert_notifications",
)


@dataclass(frozen=True)
class AbnormalLog:
    logger: str
    level: str
    source: str
    line: int


class ErrorLogCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[AbnormalLog] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(AbnormalLog(
            logger=record.name,
            level=record.levelname,
            source=record.pathname.rsplit("/", 1)[-1],
            line=record.lineno,
        ))


def main() -> int:
    root = logging.getLogger()
    previous_level = root.level
    collector = ErrorLogCollector()
    root.addHandler(collector)
    root.setLevel(min(previous_level or logging.WARNING, logging.ERROR))
    try:
        loader = unittest.defaultTestLoader
        suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in CORE_SUITES)
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    finally:
        root.removeHandler(collector)
        root.setLevel(previous_level)

    print("\nCore journey acceptance summary")
    print(f"Suites: {len(CORE_SUITES)}")
    print(f"Tests: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print(f"ERROR/CRITICAL log records: {len(collector.records)}")
    for item in collector.records:
        print(f"ABNORMAL_LOG level={item.level} logger={item.logger} source={item.source}:{item.line}")

    if not result.wasSuccessful() or collector.records:
        print("CORE_ACCEPTANCE=FAIL")
        return 1
    print("CORE_ACCEPTANCE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
