"""Password hashing and opaque credential handling using the Python standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


class CredentialError(ValueError):
    """Raised for invalid credentials or password policy violations."""


class PasswordHasher:
    """scrypt password hashing; parameters are versioned in the encoded value."""
    N = 2**15
    R = 8
    P = 1
    DKLEN = 64

    @classmethod
    def hash(cls, password: str) -> str:
        if len(password) < 12:
            raise CredentialError("password must contain at least 12 characters")
        if len(password) > 1024:
            raise CredentialError("password is too long")
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=cls.N, r=cls.R, p=cls.P, dklen=cls.DKLEN, maxmem=128 * 1024 * 1024)
        salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
        digest_text = base64.urlsafe_b64encode(derived).decode("ascii").rstrip("=")
        return "scrypt$%s$%s$%s$%s$%s" % (
            cls.N, cls.R, cls.P, salt_text, digest_text
        )

    @staticmethod
    def verify(password: str, encoded: str) -> bool:
        try:
            algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
            if algorithm != "scrypt":
                return False
            if (int(n), int(r), int(p)) != (PasswordHasher.N, PasswordHasher.R, PasswordHasher.P):
                return False

            def decode(value: str) -> bytes:
                return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            salt, expected = decode(salt_text), decode(digest_text)
            if len(salt) != 16 or len(expected) != PasswordHasher.DKLEN:
                return False
            actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected), maxmem=128 * 1024 * 1024)
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError, MemoryError):
            return False


def issue_opaque_token(prefix: str = "aps") -> str:
    return "%s_%s" % (prefix, secrets.token_urlsafe(32))


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
