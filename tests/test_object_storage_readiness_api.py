"""Readiness endpoint integration for an unavailable S3-compatible dependency."""
from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.health import create_health_router
from agent_platform.storage.object_storage import S3ObjectStorage


class FakeS3:
    def __init__(self, unavailable: bool = False):
        self.unavailable = unavailable

    async def head_bucket(self, **_kwargs):
        if self.unavailable:
            raise ConnectionError("private endpoint details")


class ObjectStorageReadinessApiTests(unittest.TestCase):
    def _client(self, unavailable: bool) -> TestClient:
        storage = S3ObjectStorage(client=FakeS3(unavailable), bucket="assets")
        app = FastAPI()
        app.include_router(create_health_router(
            readiness_checks={"object_storage": storage.check_ready},
        ))
        return TestClient(app)

    def test_storage_unavailable_makes_readiness_503_without_leaking_details(self):
        response = self._client(unavailable=True).get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["checks"]["object_storage"], "unavailable")
        self.assertNotIn("private endpoint details", response.text)

    def test_storage_available_keeps_readiness_healthy(self):
        response = self._client(unavailable=False).get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"]["object_storage"], "ok")


if __name__ == "__main__":
    unittest.main()
