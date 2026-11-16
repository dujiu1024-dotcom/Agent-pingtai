"""Explicit, loss-aware overflow handling for structured Run context.

Critical goal/constraint/decision/evidence sections are never truncated or summarized.
Only optional working context may be reduced. A host supplies token counting and,
for summarize mode, a trusted summarizer; no implicit model call is made here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence


class ContextOverflowError(ValueError):
    """The required context cannot safely fit within the effective token limit."""


class OverflowStrategy(str, Enum):
    REJECT = "reject"
    TRUNCATE = "truncate"
    SUMMARIZE = "summarize"
    HUMAN = "human"


_CRITICAL_KINDS = frozenset({"goal", "constraint", "decision", "evidence"})


@dataclass(frozen=True)
class ContextSection:
    key: str
    kind: str
    content: str
    priority: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("context section key is required")
        if self.kind not in {*_CRITICAL_KINDS, "context"}:
            raise ValueError("unsupported context section kind")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("context section content is required")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValueError("context section priority must be an integer")

    @property
    def is_critical(self) -> bool:
        return self.kind in _CRITICAL_KINDS


@dataclass(frozen=True)
class ContextBundle:
    sections: tuple[ContextSection, ...]

    def __post_init__(self) -> None:
        if not self.sections:
            raise ValueError("context bundle must contain at least one section")
        keys = [section.key for section in self.sections]
        if len(keys) != len(set(keys)):
            raise ValueError("context section keys must be unique")


@dataclass(frozen=True)
class ContextOverflowResult:
    sections: tuple[ContextSection, ...]
    strategy: OverflowStrategy
    tokens_before: int
    tokens_after: int
    preserved_keys: tuple[str, ...]
    removed_keys: tuple[str, ...] = ()
    requires_human: bool = False


TokenCounter = Callable[[Sequence[ContextSection]], int]
Summarizer = Callable[[tuple[ContextSection, ...], int], str]


def _count(counter: TokenCounter, sections: Sequence[ContextSection]) -> int:
    value = counter(sections)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContextOverflowError("token counter returned an invalid token count")
    return value


def prepare_context(
    bundle: ContextBundle,
    *,
    max_tokens: int,
    strategy: OverflowStrategy | str,
    token_counter: TokenCounter,
    summarizer: Summarizer | None = None,
) -> ContextOverflowResult:
    """Apply one explicit overflow strategy while preserving all critical sections."""
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens must be a positive integer")
    try:
        selected = OverflowStrategy(strategy)
    except (TypeError, ValueError) as exc:
        raise ValueError("unsupported context overflow strategy") from exc

    original = tuple(bundle.sections)
    before = _count(token_counter, original)
    critical = tuple(section for section in original if section.is_critical)
    preserved = tuple(section.key for section in critical)
    if before <= max_tokens:
        return ContextOverflowResult(original, selected, before, before, preserved)
    if selected is OverflowStrategy.REJECT:
        raise ContextOverflowError("context exceeds the token limit; request rejected")
    if selected is OverflowStrategy.HUMAN:
        return ContextOverflowResult(original, selected, before, before, preserved, requires_human=True)

    critical_tokens = _count(token_counter, critical)
    if critical_tokens > max_tokens:
        raise ContextOverflowError("critical goal, constraint, decision, and evidence sections exceed the token limit")

    optional = [section for section in original if not section.is_critical]
    if selected is OverflowStrategy.SUMMARIZE:
        if not optional or summarizer is None:
            raise ContextOverflowError("summarize strategy requires optional context and a summarizer")
        summary = summarizer(tuple(optional), max_tokens - critical_tokens)
        if not isinstance(summary, str) or not summary.strip():
            raise ContextOverflowError("summarizer returned an empty or invalid summary")
        replacement = ContextSection(
            key="context-summary", kind="context", content=summary.strip(),
            priority=max((section.priority for section in optional), default=0),
        )
        candidate = (*critical, replacement)
        after = _count(token_counter, candidate)
        if after > max_tokens:
            raise ContextOverflowError("summary plus critical context still exceeds the token limit")
        return ContextOverflowResult(
            tuple(candidate), selected, before, after, preserved,
            tuple(section.key for section in optional),
        )

    working = list(original)
    removed: list[str] = []
    for section in sorted(optional, key=lambda item: (item.priority, original.index(item))):
        if _count(token_counter, working) <= max_tokens:
            break
        working.remove(section)
        removed.append(section.key)
    after = _count(token_counter, working)
    if after > max_tokens:
        raise ContextOverflowError("optional context cannot be truncated to fit safely")
    return ContextOverflowResult(tuple(working), selected, before, after, preserved, tuple(removed))
