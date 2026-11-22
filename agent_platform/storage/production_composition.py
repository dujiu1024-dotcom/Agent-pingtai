"""Production composition for S3/MinIO assets, ClamAV and PostgreSQL connector jobs.

The host owns network clients and their lifecycle. This module only assembles
reviewed adapters; it never silently falls back to in-memory queues or local disk.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from agent_platform.control_plane.connector_workers import (
    DataConnectorSyncWorker,
    PostgresConnectorSyncQueue,
)
from agent_platform.control_plane.data_connectors import DataConnectorService
from agent_platform.control_plane.file_processing import (
    FileProcessingWorker,
    PostgresFileProcessingQueue,
)
from agent_platform.control_plane.sessions_assets import ObjectStorage, SessionRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.security.clamav import ClamAVScanner
from agent_platform.storage.object_storage import S3ObjectStorage


@dataclass(frozen=True)
class ProductionAssetConnectorRuntime:
    """Adapters and worker installed by the production host."""

    object_storage: S3ObjectStorage
    scanner: ClamAVScanner
    connector_queue: PostgresConnectorSyncQueue
    connector_worker: "ManagedConnectorWorker"
    file_processing_queue: PostgresFileProcessingQueue
    file_processing_worker: FileProcessingWorker


class ManagedConnectorWorker:
    """Poll a durable connector queue until cancelled; safe for host lifespan use."""

    def __init__(
        self, *, worker: DataConnectorSyncWorker, poll_interval_seconds: float = 1.0,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("connector worker poll interval must be positive")
        self.worker = worker
        self.poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run(self) -> None:
        """Run until cancelled; one failed job must not terminate the worker."""
        while not self._stop.is_set():
            try:
                processed = await self.worker.process_once()
            except Exception:
                # Database/network failures must not silently kill the managed
                # worker; back off before reconnecting through the factory.
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.poll_interval_seconds,
                    )
                except TimeoutError:
                    pass

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self.run(), name="postgres-connector-worker")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            await task
            self._task = None


def build_production_asset_connector_runtime(
    *,
    s3_client: Any,
    bucket: str,
    connection_factory: Callable[[], Any],
    connector_service: DataConnectorService,
    file_repository_for_tenant: Callable[[str], SessionRepository],
    knowledge_for_tenant: Callable[[str], KnowledgeService],
    worker_id: str,
    clamav_host: str,
    clamav_port: int = 3310,
    clamav_timeout_seconds: float = 20.0,
    clamav_max_scan_bytes: int = 25 * 1024 * 1024,
    s3_server_side_encryption: str | None = "AES256",
    s3_kms_key_id: str | None = None,
    connector_lease_seconds: int = 60,
    connector_poll_interval_seconds: float = 1.0,
) -> ProductionAssetConnectorRuntime:
    """Build production adapters and replace any non-durable connector queue.

    S3 client must be an async S3-compatible client (for example aiobotocore).
    PostgreSQL connection_factory must return an async psycopg connection.
    Missing production dependencies are rejected instead of selecting test
    implementations.
    """
    if s3_client is None or not callable(getattr(s3_client, "put_object", None)):
        raise ValueError("an async S3-compatible client is required")
    if not bucket.strip():
        raise ValueError("S3_BUCKET is required")
    if not callable(connection_factory):
        raise ValueError("PostgreSQL connection factory is required")
    if connector_service is None:
        raise ValueError("production DataConnectorService is required")
    if not callable(file_repository_for_tenant):
        raise ValueError("tenant-scoped PostgreSQL file repository resolver is required")
    if not callable(knowledge_for_tenant):
        raise ValueError("tenant-scoped KnowledgeService resolver is required")
    if not worker_id.strip():
        raise ValueError("connector worker id is required")

    storage = S3ObjectStorage(
        client=s3_client,
        bucket=bucket,
        server_side_encryption=s3_server_side_encryption,
        kms_key_id=s3_kms_key_id,
    )
    scanner = ClamAVScanner(
        host=clamav_host,
        port=clamav_port,
        timeout_seconds=clamav_timeout_seconds,
        max_file_bytes=clamav_max_scan_bytes,
    )
    queue = PostgresConnectorSyncQueue(connection_factory)
    # The API and worker must enqueue/claim from the exact same durable queue.
    connector_service.sync_queue = queue
    worker = DataConnectorSyncWorker(
        service=connector_service,
        queue=queue,
        worker_id=worker_id,
        lease_seconds=connector_lease_seconds,
    )
    file_queue = PostgresFileProcessingQueue(connection_factory=connection_factory)

    def storage_for_tenant(_tenant_id: str) -> ObjectStorage:
        # Object keys are tenant-prefixed and the S3 adapter validates the prefix.
        return storage

    file_worker = FileProcessingWorker(
        queue=file_queue,
        worker_id=worker_id,
        repository_for_tenant=file_repository_for_tenant,
        storage_for_tenant=storage_for_tenant,
        scanner=scanner,
        knowledge_for_tenant=knowledge_for_tenant,
    )
    return ProductionAssetConnectorRuntime(
        object_storage=storage,
        scanner=scanner,
        connector_queue=queue,
        connector_worker=ManagedConnectorWorker(
            worker=worker,
            poll_interval_seconds=connector_poll_interval_seconds,
        ),
        file_processing_queue=file_queue,
        file_processing_worker=file_worker,
    )
