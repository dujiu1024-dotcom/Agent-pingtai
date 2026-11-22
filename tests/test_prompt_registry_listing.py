from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.prompts import create_prompt_registry_router
from agent_platform.identity.models import Principal
from agent_platform.observability.prompts import (
    PromptRegistry,
    tenant_prompt_key,
    tenant_prompt_prefix,
)


def _principal(tenant_id: str = "tenant-a") -> Principal:
    return Principal(
        user_id="user-1",
        tenant_id=tenant_id,
        session_id="session-1",
        email="user@example.test",
        display_name="Test User",
    )


def test_prompt_registry_list_is_tenant_scoped_and_authorized() -> None:
    registry = PromptRegistry()
    registry.register(tenant_prompt_key("tenant-a", "visible"), "Hello {name}")
    registry.register(tenant_prompt_key("tenant-a", "hidden"), "Secret {value}")
    registry.register(tenant_prompt_key("tenant-b", "foreign"), "Other tenant {value}")

    def authorize(actor: Principal, prompt_id: str, action: str) -> bool:
        return actor.tenant_id == "tenant-a" and prompt_id == "visible" and action == "read"

    app = FastAPI()
    principal = _principal()

    @app.middleware("http")
    async def inject_test_principal(request, call_next):
        setattr(request.state, "identity.principal", principal)
        return await call_next(request)

    app.include_router(create_prompt_registry_router(registry=registry, authorize=authorize))
    response = TestClient(app).get("/api/platform/v1/prompts/")

    assert response.status_code == 200
    assert response.json() == {
        "items": [{
            "prompt_id": "visible",
            "latest_version": 1,
            "active_version": None,
            "version_count": 1,
        }],
        "count": 1,
    }
    assert "tenant-a" not in response.text
    assert "tenant-b" not in response.text
    assert "hidden" not in response.text
    assert "foreign" not in response.text


def test_prompt_registry_storage_listing_requires_exact_tenant_prefix() -> None:
    registry = PromptRegistry()
    registry.register(tenant_prompt_key("acme", "one"), "One")
    registry.register(tenant_prompt_key("acme-other", "two"), "Two")

    rows = registry.list_prompts(prefix=tenant_prompt_prefix("acme"), limit=10)

    assert len(rows) == 1
    assert rows[0]["storage_key"] == tenant_prompt_key("acme", "one")


def test_prompt_registry_list_rejects_invalid_prefix_and_limit() -> None:
    registry = PromptRegistry()

    for prefix in ("", "tenant:", "other:"):
        try:
            registry.list_prompts(prefix=prefix, limit=10)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid tenant prefix must be rejected")

    try:
        registry.list_prompts(prefix=tenant_prompt_prefix("acme"), limit=0)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid limit must be rejected")
