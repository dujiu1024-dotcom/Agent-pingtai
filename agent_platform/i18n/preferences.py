"""Tenant-scoped locale, timezone and region preferences."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from agent_platform.i18n.context import LocaleContext, locale_context

_REGION = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True)
class TenantLocalePreference:
    tenant_id: str
    language: str
    timezone_name: str
    region: str
    updated_by: str | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        locale_context(self.language, self.timezone_name)
        if not _REGION.fullmatch(self.region):
            raise ValueError("region must be an uppercase ISO 3166-1 alpha-2 code")


class LocalePreferenceStore(Protocol):
    async def get(self, tenant_id: str) -> TenantLocalePreference | None: ...
    async def put(self, preference: TenantLocalePreference) -> None: ...


class InMemoryLocalePreferenceStore:
    """Test/development store; production should use PostgresLocalePreferenceStore."""

    def __init__(self) -> None:
        self._items: dict[str, TenantLocalePreference] = {}

    async def get(self, tenant_id: str) -> TenantLocalePreference | None:
        return self._items.get(tenant_id)

    async def put(self, preference: TenantLocalePreference) -> None:
        self._items[preference.tenant_id] = preference


class PostgresLocalePreferenceStore:
    """PostgreSQL persistence adapter; schema is provided by migration 074."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def get(self, tenant_id: str) -> TenantLocalePreference | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id, language, timezone_name, region, updated_by
                   FROM platform_tenant_locale_preferences WHERE tenant_id=%s""",
                (tenant_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return TenantLocalePreference(
            tenant_id=str(row["tenant_id"]), language=row["language"],
            timezone_name=row["timezone_name"], region=row["region"],
            updated_by=row["updated_by"],
        )

    async def put(self, preference: TenantLocalePreference) -> None:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_tenant_locale_preferences
                       (tenant_id, language, timezone_name, region, updated_by, updated_at)
                       VALUES (%s,%s,%s,%s,%s,now())
                       ON CONFLICT (tenant_id) DO UPDATE SET
                         language=EXCLUDED.language,
                         timezone_name=EXCLUDED.timezone_name,
                         region=EXCLUDED.region,
                         updated_by=EXCLUDED.updated_by,
                         updated_at=now()""",
                    (preference.tenant_id, preference.language,
                     preference.timezone_name, preference.region, preference.updated_by),
                )


class TenantLocalePreferenceService:
    def __init__(self, *, store: LocalePreferenceStore,
                 default_language: str = "zh-CN",
                 default_timezone: str = "Asia/Shanghai",
                 default_region: str = "CN") -> None:
        self.store = store
        self.default = TenantLocalePreference(
            tenant_id="__default__", language=default_language,
            timezone_name=default_timezone, region=default_region,
        )

    async def get(self, tenant_id: str) -> TenantLocalePreference:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        return await self.store.get(tenant_id) or TenantLocalePreference(
            tenant_id=tenant_id, language=self.default.language,
            timezone_name=self.default.timezone_name, region=self.default.region,
        )

    async def update(self, *, tenant_id: str, actor_id: str, language: str,
                     timezone_name: str, region: str) -> TenantLocalePreference:
        preference = TenantLocalePreference(
            tenant_id=tenant_id, language=language,
            timezone_name=timezone_name, region=region, updated_by=actor_id,
        )
        await self.store.put(preference)
        return preference

    async def context(self, tenant_id: str) -> LocaleContext:
        preference = await self.get(tenant_id)
        return locale_context(preference.language, preference.timezone_name)
