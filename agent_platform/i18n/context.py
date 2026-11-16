"""Tenant-scoped locale and timezone formatting without process-global locale state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class LocaleContext:
    language: str
    timezone_name: str
    decimal_separator: str
    grouping_separator: str

    def __post_init__(self) -> None:
        if self.language not in {"zh-CN", "en-US", "ja-JP", "de-DE", "fr-FR"}:
            raise ValueError("unsupported tenant language")
        if len(self.decimal_separator) != 1 or len(self.grouping_separator) != 1:
            raise ValueError("number separators must be single characters")
        if self.decimal_separator == self.grouping_separator:
            raise ValueError("decimal and grouping separators must differ")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc


_DEFAULTS = {
    "zh-CN": (".", ","),
    "en-US": (".", ","),
    "ja-JP": (".", ","),
    "de-DE": (",", "."),
    "fr-FR": (",", " "),
}


def locale_context(language: str, timezone_name: str) -> LocaleContext:
    try:
        decimal_separator, grouping_separator = _DEFAULTS[language]
    except KeyError as exc:
        raise ValueError("unsupported tenant language") from exc
    return LocaleContext(language, timezone_name, decimal_separator, grouping_separator)


def format_datetime(value: datetime, context: LocaleContext) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(ZoneInfo(context.timezone_name)).isoformat(timespec="seconds")


def format_number(value: int | float | Decimal, context: LocaleContext,
                  *, decimals: int = 2) -> str:
    if not 0 <= decimals <= 8:
        raise ValueError("decimals must be between 0 and 8")
    rendered = f"{Decimal(str(value)):,.{decimals}f}"
    return rendered.replace(",", "\0").replace(".", context.decimal_separator).replace("\0", context.grouping_separator)
