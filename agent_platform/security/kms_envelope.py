"""AWS KMS envelope-encryption backend for versioned platform secrets.

Secret plaintext exists only in the caller and transient process memory. KMS
protects per-version data keys; authenticated ciphertext is stored separately.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KmsDataKeyProvider(Protocol):
    async def generate_data_key(self, *, key_id: str, encryption_context: Mapping[str, str]) -> tuple[bytes, bytes]: ...
    async def decrypt_data_key(self, *, ciphertext_blob: bytes, encryption_context: Mapping[str, str]) -> bytes: ...


class SecretCiphertextStore(Protocol):
    async def put(self, secret_ref: str, version: int, envelope: bytes) -> None: ...
    async def get(self, secret_ref: str, version: int) -> bytes | None: ...
    async def delete(self, secret_ref: str, version: int) -> None: ...


class AwsKmsDataKeyProvider:
    """Thin boto3 KMS adapter; inject a client with only GenerateDataKey/Decrypt grants."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def generate_data_key(self, *, key_id: str, encryption_context: Mapping[str, str]) -> tuple[bytes, bytes]:
        response = await asyncio.to_thread(
            self._client.generate_data_key, KeyId=key_id, KeySpec="AES_256",
            EncryptionContext=dict(encryption_context),
        )
        plaintext, encrypted = response.get("Plaintext"), response.get("CiphertextBlob")
        if not isinstance(plaintext, (bytes, bytearray)) or len(plaintext) != 32 or not isinstance(encrypted, (bytes, bytearray)):
            raise RuntimeError("KMS returned an invalid data key")
        return bytes(plaintext), bytes(encrypted)

    async def decrypt_data_key(self, *, ciphertext_blob: bytes, encryption_context: Mapping[str, str]) -> bytes:
        response = await asyncio.to_thread(
            self._client.decrypt, CiphertextBlob=ciphertext_blob,
            EncryptionContext=dict(encryption_context),
        )
        plaintext = response.get("Plaintext")
        if not isinstance(plaintext, (bytes, bytearray)) or len(plaintext) != 32:
            raise RuntimeError("KMS returned an invalid decrypted data key")
        return bytes(plaintext)


class KmsEnvelopeSecretBackend:
    """SecretBackend implementation using KMS data keys + AES-256-GCM envelopes."""

    _MAGIC = b"AKMS1"

    def __init__(self, *, kms: KmsDataKeyProvider, store: SecretCiphertextStore, key_id: str) -> None:
        if not isinstance(key_id, str) or not key_id.strip() or len(key_id) > 2048:
            raise ValueError("a configured KMS key identifier is required")
        self._kms, self._store, self._key_id = kms, store, key_id

    @staticmethod
    def _context(secret_ref: str, version: int) -> dict[str, str]:
        from agent_platform.security.secrets import SecretManager
        ref = SecretManager._validate_ref(secret_ref)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("secret version must be a positive integer")
        return {"purpose": "agent-platform-secret", "secret_ref": ref, "version": str(version)}

    async def put(self, secret_ref: str, version: int, value: str) -> None:
        context = self._context(secret_ref, version)
        if not isinstance(value, str) or not value or len(value) > 65536 or "\x00" in value:
            raise ValueError("secret value must be non-empty, NUL-free, and at most 64 KiB")
        data_key, encrypted_key = await self._kms.generate_data_key(
            key_id=self._key_id, encryption_context=context,
        )
        try:
            nonce = os.urandom(12)
            ciphertext = AESGCM(data_key).encrypt(nonce, value.encode("utf-8"),
                                                   repr(sorted(context.items())).encode("utf-8"))
            if len(encrypted_key) > 65535:
                raise ValueError("KMS encrypted data key is too large")
            envelope = self._MAGIC + len(encrypted_key).to_bytes(2, "big") + encrypted_key + nonce + ciphertext
            await self._store.put(secret_ref, version, envelope)
        finally:
            # Python cannot guarantee zeroization of immutable bytes; drop references promptly.
            data_key = b""

    async def get(self, secret_ref: str, version: int) -> str | None:
        context = self._context(secret_ref, version)
        envelope = await self._store.get(secret_ref, version)
        if envelope is None:
            return None
        if not isinstance(envelope, bytes) or len(envelope) < 5 + 2 + 12 + 16 or not envelope.startswith(self._MAGIC):
            raise RuntimeError("stored KMS secret envelope is invalid")
        key_length = int.from_bytes(envelope[5:7], "big")
        if key_length < 1 or len(envelope) < 7 + key_length + 12 + 16:
            raise RuntimeError("stored KMS secret envelope is truncated")
        encrypted_key = envelope[7:7 + key_length]
        nonce = envelope[7 + key_length:19 + key_length]
        ciphertext = envelope[19 + key_length:]
        data_key = await self._kms.decrypt_data_key(ciphertext_blob=encrypted_key, encryption_context=context)
        try:
            plaintext = AESGCM(data_key).decrypt(
                nonce, ciphertext, repr(sorted(context.items())).encode("utf-8"),
            )
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise RuntimeError("KMS secret envelope authentication failed") from exc
        finally:
            data_key = b""

    async def revoke(self, secret_ref: str, version: int) -> None:
        self._context(secret_ref, version)
        await self._store.delete(secret_ref, version)


class PostgresSecretCiphertextStore:
    """Persist ciphertext envelopes only; plaintext and KMS data keys never enter SQL."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def put(self, secret_ref: str, version: int, envelope: bytes) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_secret_ciphertexts(secret_ref,version,envelope,updated_at)
                   VALUES (%s,%s,%s,now())
                   ON CONFLICT(secret_ref,version) DO UPDATE
                     SET envelope=EXCLUDED.envelope,updated_at=now()""",
                (secret_ref, version, envelope),
            )

    async def get(self, secret_ref: str, version: int) -> bytes | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT envelope FROM platform_secret_ciphertexts WHERE secret_ref=%s AND version=%s",
                (secret_ref, version),
            )
            row = await cursor.fetchone()
        return bytes(row["envelope"]) if row else None

    async def delete(self, secret_ref: str, version: int) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                "DELETE FROM platform_secret_ciphertexts WHERE secret_ref=%s AND version=%s",
                (secret_ref, version),
            )
