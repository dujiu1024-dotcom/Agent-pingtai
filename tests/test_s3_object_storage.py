from __future__ import annotations

import io
import unittest

from agent_platform.storage.object_storage import ObjectStorageError, S3ObjectStorage


class FakeBody(io.BytesIO):
    pass


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.presign_calls = []

    async def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    async def get_object(self, **kwargs):
        return {"Body": FakeBody(self.objects[(kwargs["Bucket"], kwargs["Key"])])}

    async def delete_object(self, **kwargs):
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)

    def generate_presigned_url(self, method, *, Params, ExpiresIn):
        self.presign_calls.append((method, Params, ExpiresIn))
        return "https://storage.example.test/signed"


class S3ObjectStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_get_delete_and_server_side_encryption(self):
        client = FakeS3Client()
        storage = S3ObjectStorage(client=client, bucket="platform-assets")
        key = "tenant/tenant-a/assets/file-1/hash"
        await storage.put(key, b"payload", media_type="text/plain")
        self.assertEqual(client.put_calls[0]["ServerSideEncryption"], "AES256")
        self.assertEqual(await storage.get(key), b"payload")
        self.assertEqual(await storage.presign_get(key, expires_in=120), "https://storage.example.test/signed")
        self.assertEqual(client.presign_calls[0][0], "get_object")
        self.assertEqual(client.presign_calls[0][1], {"Bucket": "platform-assets", "Key": key})
        self.assertEqual(client.presign_calls[0][2], 120)
        with self.assertRaises(ValueError):
            await storage.presign_get(key, expires_in=901)
        await storage.delete(key)
        self.assertEqual(client.objects, {})

    async def test_kms_configuration_and_unsafe_keys(self):
        client = FakeS3Client()
        with self.assertRaises(ValueError):
            S3ObjectStorage(client=client, bucket="assets", server_side_encryption="aws:kms")
        storage = S3ObjectStorage(
            client=client, bucket="assets",
            server_side_encryption="aws:kms", kms_key_id="kms-key",
        )
        await storage.put("tenant/t1/assets/f1/hash", b"x", media_type="text/plain")
        self.assertEqual(client.put_calls[0]["SSEKMSKeyId"], "kms-key")
        for key in ("../escape", "tenant/t1/../escape", "/tenant/t1/file", "tenant//file"):
            with self.assertRaises(ObjectStorageError):
                await storage.delete(key)


if __name__ == "__main__":
    unittest.main()
