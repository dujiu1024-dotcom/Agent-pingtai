from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.localization import create_locale_preferences_router
from agent_platform.identity.models import Principal
from agent_platform.i18n.preferences import (
    InMemoryLocalePreferenceStore, TenantLocalePreferenceService,
)


class FakeAudit:
    def __init__(self):
        self.events = []
    async def record(self, **event):
        self.events.append(event)


class TenantLocalePreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_are_tenant_scoped_and_update_isolated(self):
        store = InMemoryLocalePreferenceStore()
        service = TenantLocalePreferenceService(store=store)
        default = await service.get("tenant-a")
        self.assertEqual((default.language, default.timezone_name, default.region),
                         ("zh-CN", "Asia/Shanghai", "CN"))
        await service.update(
            tenant_id="tenant-a", actor_id="admin-a",
            language="ja-JP", timezone_name="Asia/Tokyo", region="JP",
        )
        self.assertEqual((await service.get("tenant-a")).language, "ja-JP")
        self.assertEqual((await service.get("tenant-b")).language, "zh-CN")
        context = await service.context("tenant-a")
        self.assertEqual(context.language, "ja-JP")
        self.assertEqual(context.timezone_name, "Asia/Tokyo")

    async def test_invalid_language_timezone_and_region_are_rejected(self):
        service = TenantLocalePreferenceService(store=InMemoryLocalePreferenceStore())
        for values in (
            ("xx-XX", "UTC", "US"),
            ("en-US", "Not/AZone", "US"),
            ("en-US", "UTC", "usa"),
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    await service.update(
                        tenant_id="tenant-a", actor_id="admin",
                        language=values[0], timezone_name=values[1], region=values[2],
                    )

    async def test_http_api_requires_authorization_and_audit_before_write(self):
        store = InMemoryLocalePreferenceStore()
        service = TenantLocalePreferenceService(store=store)
        audit = FakeAudit()
        app = FastAPI()
        app.include_router(create_locale_preferences_router(
            service=service, authorize_manage=lambda actor: actor.user_id == "admin",
            audit_writer=audit,
        ))
        @app.middleware("http")
        async def attach_principal(request, call_next):
            setattr(request.state, "identity.principal", Principal(
                user_id="admin", tenant_id="tenant-a", session_id="s",
                email="admin@example.test", display_name="Admin",
            ))
            return await call_next(request)
        client = TestClient(app)
        current = client.get("/api/platform/v1/tenant/locale")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["language"], "zh-CN")
        changed = client.put("/api/platform/v1/tenant/locale", json={
            "language": "de-DE", "timezone": "Europe/Berlin", "region": "DE",
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["language"], "de-DE")
        self.assertEqual(audit.events[0]["action"], "tenant.locale.updated.requested")
        self.assertEqual(audit.events[0]["tenant_id"], "tenant-a")

    async def test_missing_audit_writer_fails_closed(self):
        store = InMemoryLocalePreferenceStore()
        app = FastAPI()
        app.include_router(create_locale_preferences_router(
            service=TenantLocalePreferenceService(store=store),
            authorize_manage=lambda actor: True, audit_writer=None,
        ))
        @app.middleware("http")
        async def attach_principal(request, call_next):
            setattr(request.state, "identity.principal", Principal(
                user_id="admin", tenant_id="tenant-a", session_id="s",
                email="admin@example.test", display_name="Admin",
            ))
            return await call_next(request)
        response = TestClient(app).put("/api/platform/v1/tenant/locale", json={
            "language": "en-US", "timezone": "UTC", "region": "US",
        })
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(await store.get("tenant-a"))


if __name__ == "__main__":
    unittest.main()
