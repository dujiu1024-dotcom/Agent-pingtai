"""Fail-closed token estimation for OpenAI-compatible chat requests.

The estimator counts text and serialized tool metadata using the model's tokenizer.
It deliberately rejects multimodal payloads until a provider-specific estimator is
configured; guessing image/audio token costs would undermine hard context budgets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


class TokenEstimationError(ValueError):
    """A request cannot be safely estimated with the configured tokenizer."""


@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class TiktokenChatEstimator:
    """Estimate serialized chat input plus an explicit planned output allowance."""

    def __init__(self, encoding_resolver: Callable[[str], Any] | None = None) -> None:
        self._encoding_resolver = encoding_resolver or self._resolve_encoding

    @staticmethod
    def _resolve_encoding(model_id: str) -> Any:
        try:
            import tiktoken
        except ImportError as exc:
            raise TokenEstimationError(
                "install the platform tokenizer extra (tiktoken) before enabling token budgets"
            ) from exc
        try:
            return tiktoken.encoding_for_model(model_id)
        except KeyError as exc:
            raise TokenEstimationError(
                "no tokenizer mapping is available for the selected model"
            ) from exc

    @staticmethod
    def _encode(encoding: Any, value: str) -> int:
        try:
            return len(encoding.encode(value, disallowed_special=()))
        except Exception as exc:
            raise TokenEstimationError("tokenizer failed to encode request text") from exc

    def estimate(
        self,
        *,
        model_id: str,
        messages: Sequence[Mapping[str, Any]],
        request_parameters: Mapping[str, Any],
        context_window_tokens: int,
    ) -> TokenEstimate:
        if not isinstance(model_id, str) or not model_id.strip():
            raise TokenEstimationError("model_id is required for tokenizer selection")
        if not isinstance(context_window_tokens, int) or isinstance(context_window_tokens, bool) or context_window_tokens < 1:
            raise TokenEstimationError("context window must be a positive integer")
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not messages:
            raise TokenEstimationError("at least one chat message is required")
        if not isinstance(request_parameters, Mapping):
            raise TokenEstimationError("request parameters must be a mapping")
        output_keys = [key for key in ("max_completion_tokens", "max_tokens") if key in request_parameters]
        if len(output_keys) != 1:
            raise TokenEstimationError("exactly one explicit max_completion_tokens or max_tokens is required")
        output_tokens = request_parameters[output_keys[0]]
        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool) or output_tokens < 1:
            raise TokenEstimationError("planned output tokens must be a positive integer")
        if output_tokens >= context_window_tokens:
            raise TokenEstimationError("planned output allowance must be smaller than the model context window")

        encoding = self._encoding_resolver(model_id)
        input_tokens = 3  # assistant reply priming
        for message in messages:
            if not isinstance(message, Mapping):
                raise TokenEstimationError("chat messages must be mappings")
            role = message.get("role")
            if not isinstance(role, str) or not role:
                raise TokenEstimationError("each chat message requires a role")
            input_tokens += 3 + self._encode(encoding, role)
            for key in ("name", "content"):
                value = message.get(key)
                if value is None:
                    continue
                if key == "name":
                    if not isinstance(value, str):
                        raise TokenEstimationError("message name must be text")
                    input_tokens += 1 + self._encode(encoding, value)
                elif isinstance(value, str):
                    input_tokens += self._encode(encoding, value)
                elif isinstance(value, list):
                    for part in value:
                        if not isinstance(part, Mapping) or part.get("type") != "text" or not isinstance(part.get("text"), str):
                            raise TokenEstimationError("multimodal message content requires a provider-specific estimator")
                        input_tokens += self._encode(encoding, part["text"])
                else:
                    raise TokenEstimationError("message content must be text or text-only content parts")
            for key in ("tool_calls", "function_call"):
                if key in message and message[key] is not None:
                    try:
                        serialized = json.dumps(message[key], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    except (TypeError, ValueError) as exc:
                        raise TokenEstimationError("tool metadata is not safely serializable") from exc
                    input_tokens += self._encode(encoding, serialized)
            for key, value in message.items():
                if key in {"role", "name", "content", "tool_calls", "function_call"} or value is None:
                    continue
                if key.lower() in {"image", "image_url", "audio", "input_audio", "video"}:
                    raise TokenEstimationError("multimodal message metadata requires a provider-specific estimator")
                if isinstance(value, str):
                    input_tokens += self._encode(encoding, value)
                else:
                    try:
                        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    except (TypeError, ValueError) as exc:
                        raise TokenEstimationError("additional message metadata is not safely serializable") from exc
                    input_tokens += self._encode(encoding, serialized)
        if input_tokens + output_tokens > context_window_tokens:
            raise TokenEstimationError("estimated prompt plus planned output exceeds model context window")
        return TokenEstimate(input_tokens=input_tokens, output_tokens=output_tokens)
