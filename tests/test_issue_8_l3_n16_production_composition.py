from __future__ import annotations

import unittest

from agent_platform.control_plane.connector_workers import PostgresConnectorSyncQueue
from agent_platform.control_plane.data_connectors import DataConnectorService
from agent_platform.security.clamav import ClamAVScanner
from agent_platform.storage.object_storage import S3ObjectStorage
from agent_platform.storage.production_composition import (
    ManagedConnectorWorker,
    build_production_asset_connector_runtime,
)


class AsyncS3Client:
    async def put_object(self, **kwargs):
        return None


class ProductionAssetConnectorCompositionTests(unittest.TestCase):
    def test_builds_s3_clamav_and_postgres_queue_without_in_memory_fallback(self):
        service = DataConnectorService(
            repository=object(), providers={}, secrets=object(), sink=object(),
        )
        runtime = build_production_asset_connector_runtime(
            s3_client=AsyncS3Client(),
            bucket="platform-assets",
            connection_factory=lambda: object(),
            connector_service=service,
            worker_id="connector-worker-1",
            clamav_host="clamav",
        )
        self.assertIsInstance(runtime.object_storage, S3ObjectStorage)
        self.assertIsInstance(runtime.scanner, ClamAVScanner)
        self.assertIsInstance(runtime.connector_queue, PostgresConnectorSyncQueue)
        self.assertIsInstance(runtime.connector_worker, ManagedConnectorWorker)
        self.assertIs(service.sync_queue, runtime.connector_queue)
        self.assertIs(runtime.connector_worker.worker.queue, runtime.connector_queue)

    def test_missing_production_dependencies_fail_closed(self):
        service = DataConnectorService(
            repository=object(), providers={}, secrets=object(), sink=object(),
        )
        cases = (
            {"s3_client": None},
            {"bucket": " "},
            {"connection_factory": None},
            {"worker_id": ""},
            {"clamav_host": ""},
        )
        base = {
            "s3_client": AsyncS3Client(),
            "bucket": "assets",
            "connection_factory": lambda: object(),
            "connector_service": service,
            "worker_id": "worker",
            "clamav_host": "clamav",
        }
        for override in cases:
            with self.subTest(override=override), self.assertRaises((ValueError, AttributeError)):
                build_production_asset_connector_runtime(**(base | override))


if __name__ == "__main__":
    unittest.main()
