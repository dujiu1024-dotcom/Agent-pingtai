from __future__ import annotations

import unittest

from agent_platform.quotas.storage_meter import S3TenantStorageMeter


class FakeS3:
    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    async def list_objects_v2(self, **kwargs):
        self.requests.append(kwargs)
        return self.pages.pop(0)


class S3TenantStorageMeterTests(unittest.IsolatedAsyncioTestCase):
    async def test_measures_all_pages_for_only_the_tenant_prefix(self):
        client = FakeS3([
            {
                "Contents": [
                    {"Key": "tenant/tenant-a/a.bin", "Size": 10},
                    {"Key": "tenant/tenant-a/b.bin", "Size": 20},
                ],
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
            },
            {
                "Contents": [{"Key": "tenant/tenant-a/c.bin", "Size": 5}],
                "IsTruncated": False,
            },
        ])
        meter = S3TenantStorageMeter(client=client, bucket="platform", max_pages=2)
        result = await meter.measure(tenant_id="tenant-a")
        self.assertEqual(result.tenant_id, "tenant-a")
        self.assertEqual(result.object_count, 3)
        self.assertEqual(result.storage_bytes, 35)
        self.assertEqual(client.requests[0], {
            "Bucket": "platform", "Prefix": "tenant/tenant-a/",
        })
        self.assertEqual(client.requests[1]["ContinuationToken"], "page-2")

    async def test_rejects_unsafe_tenant_ids_before_listing(self):
        client = FakeS3([])
        meter = S3TenantStorageMeter(client=client, bucket="platform")
        for tenant_id in ("", "../other", "tenant/a", "tenant a"):
            with self.subTest(tenant_id=tenant_id):
                with self.assertRaises(ValueError):
                    await meter.measure(tenant_id=tenant_id)
        self.assertEqual(client.requests, [])

    async def test_rejects_cross_tenant_objects_and_invalid_sizes(self):
        for entry in (
            {"Key": "tenant/tenant-b/other.bin", "Size": 1},
            {"Key": "tenant/tenant-a/bad.bin", "Size": True},
            {"Key": "tenant/tenant-a/bad.bin", "Size": -1},
        ):
            with self.subTest(entry=entry):
                client = FakeS3([{"Contents": [entry], "IsTruncated": False}])
                meter = S3TenantStorageMeter(client=client, bucket="platform")
                with self.assertRaises(ValueError):
                    await meter.measure(tenant_id="tenant-a")

    async def test_rejects_repeated_tokens_duplicate_keys_and_page_limit(self):
        repeated = FakeS3([
            {"Contents": [], "IsTruncated": True, "NextContinuationToken": "same"},
            {"Contents": [], "IsTruncated": True, "NextContinuationToken": "same"},
        ])
        with self.assertRaisesRegex(ValueError, "repeated"):
            await S3TenantStorageMeter(client=repeated, bucket="platform").measure(
                tenant_id="tenant-a",
            )

        duplicate = FakeS3([
            {"Contents": [{"Key": "tenant/tenant-a/a", "Size": 1}],
             "IsTruncated": True, "NextContinuationToken": "next"},
            {"Contents": [{"Key": "tenant/tenant-a/a", "Size": 1}],
             "IsTruncated": False},
        ])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            await S3TenantStorageMeter(client=duplicate, bucket="platform").measure(
                tenant_id="tenant-a",
            )

        limited = FakeS3([
            {"Contents": [], "IsTruncated": True, "NextContinuationToken": "next"},
        ])
        with self.assertRaisesRegex(RuntimeError, "page limit"):
            await S3TenantStorageMeter(
                client=limited, bucket="platform", max_pages=1,
            ).measure(tenant_id="tenant-a")


if __name__ == "__main__":
    unittest.main()
