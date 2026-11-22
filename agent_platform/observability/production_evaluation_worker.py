"""Standalone production worker for pinned Agent/RAG evaluation schedules."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import signal
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import entry_points
from typing import Any

from agent_platform.observability.evaluation import EvalGatePolicy
from agent_platform.observability.evaluation_leases import PostgresEvaluationScheduleLeaseStore
from agent_platform.observability.evaluation_runner import EvaluationRunService
from agent_platform.observability.evaluation_store import PostgresEvaluationRepository
from agent_platform.observability.evaluation_worker import (
    EvaluationSchedule,
    EvaluationScheduleWorker,
)
from agent_platform.production_config import load_production_settings

logger = logging.getLogger("agent_platform.evaluation_scheduler")


@dataclass(frozen=True)
class EvaluationWorkerSettings:
    schedules: tuple[EvaluationSchedule, ...]
    runner_entrypoint: str
    poll_interval_seconds: float = 5.0
    max_concurrent_runs: int = 2
    lease_seconds: float = 120.0
    evaluation_concurrency: int = 4
    min_pass_rate: float = 0.95
    min_average_score: float = 0.9
    min_average_recall_at_k: float = 0.8


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def load_evaluation_worker_settings(
    environ: Mapping[str, str] | None = None,
) -> EvaluationWorkerSettings:
    env = os.environ if environ is None else environ
    runner_name = env.get("EVALUATION_RUNNER_ENTRYPOINT", "").strip()
    if not runner_name:
        raise ValueError("EVALUATION_RUNNER_ENTRYPOINT is required")
    raw = env.get("EVALUATION_SCHEDULES_JSON", "").strip()
    if not raw:
        raise ValueError("EVALUATION_SCHEDULES_JSON is required")
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("EVALUATION_SCHEDULES_JSON must be valid JSON") from exc
    if not isinstance(entries, list) or not entries:
        raise ValueError("EVALUATION_SCHEDULES_JSON must be a non-empty array")

    schedules = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"schedules[{index}] must be an object")
        for key in ("schedule_id", "tenant_id", "dataset_id", "model_key"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"schedules[{index}].{key} is required")
        version = entry.get("dataset_version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError(f"schedules[{index}].dataset_version must be a positive integer")
        interval = _number(entry.get("interval_seconds"), f"schedules[{index}].interval_seconds",
                           1, 31_536_000)
        schedules.append(EvaluationSchedule(
            schedule_id=entry["schedule_id"].strip(), tenant_id=entry["tenant_id"].strip(),
            dataset_id=entry["dataset_id"].strip(), dataset_version=version,
            model_key=entry["model_key"].strip(), interval=timedelta(seconds=interval),
        ))
    if len({item.schedule_id for item in schedules}) != len(schedules):
        raise ValueError("evaluation schedule IDs must be unique")

    try:
        max_runs = int(env.get("EVALUATION_SCHEDULER_MAX_CONCURRENT_RUNS", "2"))
        case_concurrency = int(env.get("EVALUATION_CASE_CONCURRENCY", "4"))
    except ValueError as exc:
        raise ValueError("evaluation concurrency settings must be integers") from exc
    if not 1 <= max_runs <= 16 or not 1 <= case_concurrency <= 32:
        raise ValueError("evaluation concurrency settings are out of range")
    return EvaluationWorkerSettings(
        schedules=tuple(schedules), runner_entrypoint=runner_name,
        poll_interval_seconds=_number(env.get("EVALUATION_SCHEDULER_POLL_SECONDS", "5"),
                                      "EVALUATION_SCHEDULER_POLL_SECONDS", 0.1, 3600),
        lease_seconds=_number(env.get("EVALUATION_SCHEDULER_LEASE_SECONDS", "120"),
                               "EVALUATION_SCHEDULER_LEASE_SECONDS", 3, 3600),
        max_concurrent_runs=max_runs, evaluation_concurrency=case_concurrency,
        min_pass_rate=_number(env.get("EVALUATION_MIN_PASS_RATE", "0.95"),
                              "EVALUATION_MIN_PASS_RATE", 0, 1),
        min_average_score=_number(env.get("EVALUATION_MIN_AVERAGE_SCORE", "0.9"),
                                  "EVALUATION_MIN_AVERAGE_SCORE", 0, 1),
        min_average_recall_at_k=_number(env.get("EVALUATION_MIN_AVERAGE_RECALL_AT_K", "0.8"),
                                        "EVALUATION_MIN_AVERAGE_RECALL_AT_K", 0, 1),
    )


def load_evaluation_case_runner(name: str) -> Any:
    matches = [item for item in entry_points(group="agent_platform.evaluation_case_runner")
               if item.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one installed evaluation runner named {name!r}")
    loaded = matches[0].load()
    runner = loaded if callable(getattr(loaded, "run_case", None)) else loaded()
    if not callable(getattr(runner, "run_case", None)):
        raise TypeError("evaluation runner must expose run_case()")
    return runner


def build_evaluation_schedule_worker(
    *, connection_factory: Any, runner: Any, settings: EvaluationWorkerSettings,
) -> EvaluationScheduleWorker:
    if not callable(getattr(runner, "run_case", None)):
        raise TypeError("runner must expose run_case()")
    repository = PostgresEvaluationRepository(connection_factory)
    service = EvaluationRunService(
        repository=repository, runner=runner,
        policy=EvalGatePolicy(
            min_pass_rate=settings.min_pass_rate,
            min_average_score=settings.min_average_score,
            min_average_recall_at_k=settings.min_average_recall_at_k,
        ),
        concurrency=settings.evaluation_concurrency,
    )
    return EvaluationScheduleWorker(
        service=service, schedules=settings.schedules,
        poll_interval_seconds=settings.poll_interval_seconds,
        max_concurrent_runs=settings.max_concurrent_runs,
        lease_store=PostgresEvaluationScheduleLeaseStore(connection_factory),
        lease_seconds=settings.lease_seconds,
        owner_id=f"{socket.gethostname()}:{os.getpid()}",
    )


async def _connect_postgres(dsn: str) -> Any:
    try:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("install psycopg[binary,pool] to run evaluation scheduler") from exc
    return await AsyncConnection.connect(dsn, row_factory=dict_row)


async def run_evaluation_scheduler() -> None:
    config = load_evaluation_worker_settings()
    production = load_production_settings()

    async def connect() -> Any:
        return await _connect_postgres(production.postgres_dsn)

    runner = load_evaluation_case_runner(config.runner_entrypoint)
    worker = build_evaluation_schedule_worker(connection_factory=connect, runner=runner,
                                              settings=config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("evaluation scheduler started schedules=%d", len(config.schedules))
    try:
        await worker.run_forever(stop_event=stop)
    finally:
        logger.info("evaluation scheduler stopped cycles=%d", worker.cycles)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    asyncio.run(run_evaluation_scheduler())


if __name__ == "__main__":
    main()
