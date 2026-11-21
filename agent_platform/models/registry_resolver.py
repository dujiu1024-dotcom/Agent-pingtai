"""Resolve model runtime configuration from a Run-pinned registry snapshot."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from urllib.parse import unquote, urlsplit


class ModelRegistryError(ValueError):
    pass


class ModelSecretResolver(Protocol):
    async def resolve(self, secret_ref: str) -> str: ...


@dataclass(frozen=True)
class ResolvedModelConfig:
    template_id: str
    template_version: int
    provider_id: str
    provider_version: int
    provider_type: str
    base_url: str
    model_id: str
    api_key: str = field(repr=False, compare=False)
    parameters: Mapping[str, Any]
    capabilities: tuple[str, ...]
    allowed_data_levels: tuple[str, ...]
    context_window_tokens: int = 8192


class RegistryModelResolver:
    """Resolve model and credentials only from the immutable Run snapshot."""
    def __init__(
        self, secrets: ModelSecretResolver, *, allow_insecure_loopback: bool = False,
    ) -> None:
        self.secrets = secrets
        self.allow_insecure_loopback = allow_insecure_loopback

    @staticmethod
    def _find(
        entries: tuple[Mapping[str, Any], ...], *, kind: str, item_id: str,
    ) -> Mapping[str, Any]:
        matches = [
            entry for entry in entries
            if entry.get("kind") == kind and entry.get("item_id") == item_id
        ]
        if len(matches) != 1:
            raise ModelRegistryError(f"Run snapshot must contain exactly one {kind}:{item_id}")
        return matches[0]

    async def resolve(
        self, *, snapshot: Any, template_id: str,
    ) -> ResolvedModelConfig:
        template_entry = self._find(
            snapshot.entries, kind="model_template", item_id=template_id,
        )
        if template_entry is None:
            raise ModelRegistryError(f"model template '{template_id}' not found in Run-pinned snapshot")
        template = template_entry.get("definition")
        if not isinstance(template, Mapping):
            raise ModelRegistryError("model template definition is invalid")
        provider_id = template.get("provider_id")
        provider_version = template.get("provider_version")
        model_id = template.get("model_id")
        parameters = template.get("parameters", {})
        if (
            not isinstance(provider_id, str) or not provider_id
            or not isinstance(provider_version, int) or provider_version < 1
            or not isinstance(model_id, str) or not model_id
            or not isinstance(parameters, Mapping)
        ):
            raise ModelRegistryError("model template requires provider_id, provider_version, model_id and parameters")
        provider_entry = self._find(
            snapshot.entries, kind="model_provider", item_id=provider_id,
        )
        if provider_entry.get("version") != provider_version:
            raise ModelRegistryError("model provider version does not match the Run-pinned template")
        provider = provider_entry.get("definition")
        if not isinstance(provider, Mapping):
            raise ModelRegistryError("model provider definition is invalid")
        provider_type = provider.get("provider_type")
        base_url = provider.get("base_url")
        secret_ref = provider.get("secret_ref")
        if provider_type != "openai-compatible":
            raise ModelRegistryError("unsupported model provider type")
        if not isinstance(base_url, str):
            raise ModelRegistryError("model provider base_url is required")
        parsed = urlsplit(base_url)
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            (parsed.scheme != "https" and not (
                self.allow_insecure_loopback and parsed.scheme == "http" and loopback
            ))
            or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
        ):
            raise ModelRegistryError("model provider base_url must be HTTPS")
        tenant_id = getattr(snapshot, "tenant_id", None)
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ModelRegistryError("Run snapshot tenant_id is required for secret resolution")
        # secret_ref is optional — local providers (Ollama, etc.) have no API key.
        api_key = ""
        if isinstance(secret_ref, str) and secret_ref.strip():
            secret = urlsplit(secret_ref)
            secret_path = unquote(secret.path)
            if (
                secret.scheme != "secret" or secret.netloc != tenant_id
                or not secret_path.strip("/")
                or any(part in {".", ".."} for part in secret_path.split("/"))
                or secret.username is not None or secret.password is not None
                or secret.query or secret.fragment
            ):
                raise ModelRegistryError("model provider secret_ref must be scoped to the Run tenant")
            api_key = await self.secrets.resolve(secret_ref)
            if not isinstance(api_key, str) or not api_key.strip():
                raise ModelRegistryError("model provider secret could not be resolved")
        context_window_tokens = template.get("context_window_tokens", 8192)
        if (
            not isinstance(context_window_tokens, int)
            or isinstance(context_window_tokens, bool)
            or context_window_tokens < 1
        ):
            raise ModelRegistryError("model template context_window_tokens must be a positive integer")
        capabilities = template.get("capabilities", [])
        data_levels = template.get("allowed_data_levels", [])
        if (
            not isinstance(capabilities, list) or any(not isinstance(v, str) for v in capabilities)
            or not isinstance(data_levels, list) or any(not isinstance(v, str) for v in data_levels)
        ):
            raise ModelRegistryError("model template capabilities and data levels must be string arrays")
        return ResolvedModelConfig(
            template_id=template_id, template_version=int(template_entry["version"]),
            provider_id=provider_id, provider_version=provider_version,
            provider_type=provider_type, base_url=base_url, model_id=model_id,
            api_key=api_key, parameters=dict(parameters),
            capabilities=tuple(capabilities), allowed_data_levels=tuple(data_levels),
            context_window_tokens=context_window_tokens,
        )
