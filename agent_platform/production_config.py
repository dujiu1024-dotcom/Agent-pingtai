"""Strict environment parsing shared by the production API and worker entrypoints."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import quote, urlsplit


class ProductionConfigurationError(ValueError):
    """Raised when a production process cannot be safely configured."""


def _secret(environ: Mapping[str, str], *, value_key: str, file_key: str) -> str:
    direct = environ.get(value_key, "").strip()
    secret_file = environ.get(file_key, "").strip()
    if direct and secret_file:
        raise ProductionConfigurationError(
            f"configure only one of {value_key} or {file_key}"
        )
    if secret_file:
        try:
            direct = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ProductionConfigurationError(
                f"cannot read required secret file configured by {file_key}"
            ) from exc
    if not direct:
        raise ProductionConfigurationError(
            f"missing required configuration: {value_key} or {file_key}"
        )
    return direct


@dataclass(frozen=True)
class ProductionSettings:
    postgres_dsn: str
    redis_url: str
    allowed_origins: tuple[str, ...]


def load_production_settings(
    environ: Mapping[str, str] | None = None,
) -> ProductionSettings:
    """Load settings and fail fast instead of serving a partially wired API."""
    env = os.environ if environ is None else environ
    postgres_dsn = env.get("AGENT_PLATFORM_DSN", "").strip() or env.get(
        "DATABASE_URL", ""
    ).strip()
    if not postgres_dsn:
        host = env.get("POSTGRES_HOST", "").strip()
        database = env.get("POSTGRES_DB", "").strip()
        username = env.get("POSTGRES_USER", "").strip()
        if not all((host, database, username)):
            raise ProductionConfigurationError(
                "missing required configuration: AGENT_PLATFORM_DSN or "
                "DATABASE_URL, or POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER"
            )
        password = _secret(
            env, value_key="POSTGRES_PASSWORD", file_key="POSTGRES_PASSWORD_FILE"
        )
        port = env.get("POSTGRES_PORT", "5432").strip()
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ProductionConfigurationError("POSTGRES_PORT must be between 1 and 65535")
        postgres_dsn = (
            f"postgresql://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{host}:{port}/{quote(database, safe='')}"
        )

    redis_url = env.get("REDIS_URL", "").strip()
    if not redis_url:
        host = env.get("REDIS_HOST", "").strip()
        if not host:
            raise ProductionConfigurationError(
                "missing required configuration: REDIS_URL or REDIS_HOST"
            )
        password = _secret(
            env, value_key="REDIS_PASSWORD", file_key="REDIS_PASSWORD_FILE"
        )
        port = env.get("REDIS_PORT", "6379").strip()
        database = env.get("REDIS_DB", "0").strip()
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ProductionConfigurationError("REDIS_PORT must be between 1 and 65535")
        if not database.isdigit():
            raise ProductionConfigurationError("REDIS_DB must be a non-negative integer")
        redis_url = (
            f"redis://:{quote(password, safe='')}@{host}:{port}/{database}"
        )

    origins_value = env.get("ALLOWED_ORIGINS", "").strip()
    origins = tuple(item.strip() for item in origins_value.split(",") if item.strip())
    if not origins:
        raise ProductionConfigurationError(
            "missing required configuration: ALLOWED_ORIGINS"
        )
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            valid = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
                and "*" not in parsed.netloc
            )
        except ValueError:
            valid = False
        if not valid:
            raise ProductionConfigurationError(
                "ALLOWED_ORIGINS must contain exact HTTPS origins"
            )
    return ProductionSettings(
        postgres_dsn=postgres_dsn,
        redis_url=redis_url,
        allowed_origins=origins,
    )
