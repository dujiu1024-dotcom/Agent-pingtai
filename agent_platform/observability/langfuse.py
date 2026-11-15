"""Optional Langfuse v3 adapter; no SDK import is required for unit tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Protocol


class Observation(Protocol):
    def update(self, **kwargs: Any) -> Any: ...


class ObservationSink(Protocol):
    def observe(
        self, *, name: str, input: Mapping[str, Any], metadata: Mapping[str, Any]
    ) -> Any: ...


class LangfuseObservationSink:
    """Create root spans using the supported Langfuse Python SDK v3 client API."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from langfuse import get_client
            except ImportError as exc:
                raise RuntimeError("Install langfuse>=3,<4 to enable Langfuse tracing") from exc
            self._client = get_client()
        return self._client

    @contextmanager
    def observe(
        self, *, name: str, input: Mapping[str, Any], metadata: Mapping[str, Any]
    ) -> Iterator[Observation]:
        client = self._get_client()
        with client.start_as_current_observation(
            as_type="span",
            name=name,
            input=dict(input),
            metadata=dict(metadata),
        ) as observation:
            yield observation
