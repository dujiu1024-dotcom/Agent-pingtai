"""Fail-closed context-window overflow policies for model requests.

Protected Run anchors are carried as an explicit system message so trimming or
external summarization cannot silently discard the goal, constraints, decisions,
or evidence supplied by the Run host.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


class ContextOverflowError(ValueError):
    """A request cannot be safely reduced to the selected model's context window."""


class HumanInterventionRequired(ContextOverflowError):
    """The request requires an operator decision before model dispatch."""


@dataclass(frozen=True)
class ContextPreparation:
    messages: tuple[Mapping[str, Any], ...]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    policy: str
    changed: bool
    removed_messages: int = 0


Summarizer = Callable[..., Any]
InterventionHandler = Callable[..., Any]

_PROTECTED_FIELDS = ("goal", "constraints", "decisions", "evidence")
_MAX_ESTIMATION_WINDOW = 2_147_483_647


def _protected_message(anchors: Mapping[str, Any]) -> Mapping[str, str]:
    if not isinstance(anchors, Mapping):
        raise ContextOverflowError("protected context anchors must be a mapping")
    normalized: dict[str, Any] = {}
    for field in _PROTECTED_FIELDS:
        if field not in anchors:
            raise ContextOverflowError(f"protected context is missing {field}")
        value = anchors[field]
        if value is None or value == "" or value == [] or value == {}:
            raise ContextOverflowError(f"protected {field} must contain an explicit value")
        try:
            json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ContextOverflowError(f"protected {field} are not safely serializable") from exc
        normalized[field] = value
    serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(serialized) > 24_000:
        raise ContextOverflowError("protected context anchors exceed the safe size limit")
    return {
        "role": "system",
        "content": "Protected Run context. Preserve these facts verbatim in all continuation and compression:\n" + serialized,
    }


def _validated_estimate(estimate: Any, context_window_tokens: int) -> tuple[int, int, int]:
    input_tokens = getattr(estimate, "input_tokens", None)
    output_tokens = getattr(estimate, "output_tokens", None)
    total_tokens = getattr(estimate, "total_tokens", None)
    if (
        not isinstance(input_tokens, int) or isinstance(input_tokens, bool) or input_tokens < 0
        or not isinstance(output_tokens, int) or isinstance(output_tokens, bool) or output_tokens < 1
        or not isinstance(total_tokens, int) or isinstance(total_tokens, bool)
        or total_tokens != input_tokens + output_tokens
    ):
        raise ContextOverflowError("token estimator returned an invalid estimate")
    return input_tokens, output_tokens, total_tokens


async def _estimate(
    estimator: Any, *, model_id: str, messages: Sequence[Mapping[str, Any]],
    request_parameters: Mapping[str, Any], context_window_tokens: int,
    allow_overflow_count: bool = False,
) -> tuple[int, int, int]:
    try:
        estimate = estimator.estimate(
            model_id=model_id, messages=messages,
            request_parameters=request_parameters,
            context_window_tokens=context_window_tokens,
        )
    except Exception as exc:
        # The built-in estimator reports this one specific condition after it has
        # successfully tokenized the complete request. Re-run with a large ceiling
        # to obtain the exact count needed by the selected overflow policy. Unknown
        # tokenizer/encoding/serialization errors remain fail-closed.
        if "estimated prompt plus planned output exceeds model context window" not in str(exc):
            raise
        if not allow_overflow_count:
            raise ContextOverflowError(
                "estimated prompt plus planned output exceeds model context window; policy=reject"
            ) from exc
        estimate = estimator.estimate(
            model_id=model_id, messages=messages,
            request_parameters=request_parameters,
            context_window_tokens=_MAX_ESTIMATION_WINDOW,
        )
    if inspect.isawaitable(estimate):
        estimate = await estimate
    counts = _validated_estimate(estimate, context_window_tokens)
    return counts


async def prepare_context(
    *,
    estimator: Any,
    model_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_parameters: Mapping[str, Any],
    context_window_tokens: int,
    policy: str = "reject",
    protected_context: Mapping[str, Any] | None = None,
    summarizer: Summarizer | None = None,
    intervention_handler: InterventionHandler | None = None,
) -> ContextPreparation:
    """Estimate and, only when explicitly configured, reduce an oversized request.

    Supported policies are reject, truncate, summarize, and human_intervention.
    The latter two require host-provided callbacks; this module never calls a
    Provider to summarize or silently guesses that an operator has approved edits.
    """
    if policy not in {"reject", "truncate", "summarize", "human_intervention"}:
        raise ContextOverflowError("unsupported context overflow policy")
    if not isinstance(context_window_tokens, int) or isinstance(context_window_tokens, bool) or context_window_tokens < 1:
        raise ContextOverflowError("context window must be a positive integer")
    if not messages:
        raise ContextOverflowError("model request messages are required")
    original = tuple(dict(message) for message in messages)
    counts = await _estimate(
        estimator, model_id=model_id, messages=original,
        request_parameters=request_parameters,
        context_window_tokens=context_window_tokens,
        allow_overflow_count=policy != "reject",
    )
    if counts[2] <= context_window_tokens:
        return ContextPreparation(original, *counts, policy, False)
    if policy == "reject":
        raise ContextOverflowError("model token estimate is invalid or exceeds the pinned context window; policy=reject")
    if protected_context is None:
        raise ContextOverflowError("non-reject overflow policies require protected goal, constraints, decisions, and evidence")
    anchor = _protected_message(protected_context)
    if policy == "truncate":
        # Keep every system instruction, the latest non-system message, and the
        # protected anchor. Add older conversational turns from newest to oldest
        # only while the complete request remains within the selected model window.
        system_messages = [m for m in original if m.get("role") == "system"]
        other_messages = [m for m in original if m.get("role") != "system"]
        if not other_messages:
            raise ContextOverflowError("truncation cannot preserve a latest user/task message")
        latest = other_messages[-1]
        kept = [*system_messages, anchor, latest]
        kept_counts = await _estimate(
            estimator, model_id=model_id, messages=kept,
            request_parameters=request_parameters,
            context_window_tokens=context_window_tokens,
        )
        if kept_counts[2] > context_window_tokens:
            raise ContextOverflowError("protected instructions and latest message exceed model context window")
        retained_older: list[Mapping[str, Any]] = []
        for message in reversed(other_messages[:-1]):
            candidate = [*system_messages, anchor, message, *reversed(retained_older), latest]
            candidate_counts = await _estimate(
                estimator, model_id=model_id, messages=candidate,
                request_parameters=request_parameters,
                context_window_tokens=context_window_tokens,
                allow_overflow_count=True,
            )
            if candidate_counts[2] <= context_window_tokens:
                retained_older.insert(0, message)
                kept = candidate
                kept_counts = candidate_counts
            else:
                break
        return ContextPreparation(
            tuple(kept), *kept_counts, policy, True,
            removed_messages=len(original) - (len(kept) - 1),
        )
    if policy == "summarize":
        if not callable(summarizer):
            raise ContextOverflowError("summarize policy requires a trusted summarizer callback")
        reduced = summarizer(
            messages=original, protected_context=dict(protected_context),
            model_id=model_id, max_total_tokens=context_window_tokens,
        )
        if inspect.isawaitable(reduced):
            reduced = await reduced
        if not isinstance(reduced, Sequence) or isinstance(reduced, (str, bytes)) or not reduced:
            raise ContextOverflowError("summarizer returned an invalid message sequence")
        candidate = [anchor, *(dict(message) for message in reduced)]
        reduced_counts = await _estimate(
            estimator, model_id=model_id, messages=candidate,
            request_parameters=request_parameters,
            context_window_tokens=context_window_tokens,
            allow_overflow_count=True,
        )
        if reduced_counts[2] > context_window_tokens:
            raise ContextOverflowError("summarized context still exceeds model context window")
        return ContextPreparation(tuple(candidate), *reduced_counts, policy, True,
                                  removed_messages=max(0, len(original) - len(reduced)))
    if not callable(intervention_handler):
        raise HumanInterventionRequired("context overflow requires human intervention; no operator handler is configured")
    approved = intervention_handler(
        model_id=model_id, messages=original,
        protected_context=dict(protected_context),
        estimated_total_tokens=counts[2], context_window_tokens=context_window_tokens,
    )
    if inspect.isawaitable(approved):
        approved = await approved
    if not isinstance(approved, Sequence) or isinstance(approved, (str, bytes)) or not approved:
        raise HumanInterventionRequired("human intervention did not provide an approved message sequence")
    candidate = [anchor, *(dict(message) for message in approved)]
    approved_counts = await _estimate(
        estimator, model_id=model_id, messages=candidate,
        request_parameters=request_parameters,
        context_window_tokens=context_window_tokens,
    )
    if approved_counts[2] > context_window_tokens:
        raise HumanInterventionRequired("human-approved context still exceeds model context window")
    return ContextPreparation(tuple(candidate), *approved_counts, policy, True,
                              removed_messages=max(0, len(original) - len(approved)))
