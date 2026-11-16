from __future__ import annotations

import unittest
from datetime import date, time, timezone

from agent_platform.i18n import (
    format_currency,
    format_date,
    format_time,
    locale_context,
)


class LocaleFormattingTests(unittest.TestCase):
    def test_date_format_is_language_specific(self):
        day = date(2026, 11, 16)
        self.assertEqual(format_date(day, locale_context("zh-CN", "Asia/Shanghai")),
                         "2026年11月16日")
        self.assertEqual(format_date(day, locale_context("ja-JP", "Asia/Tokyo")),
                         "2026年11月16日")
        self.assertEqual(format_date(day, locale_context("en-US", "America/New_York")),
                         "11/16/2026")
        self.assertEqual(format_date(day, locale_context("de-DE", "Europe/Berlin")),
                         "16.11.2026")
        self.assertEqual(format_date(day, locale_context("fr-FR", "Europe/Paris")),
                         "16/11/2026")

    def test_time_and_currency_format_follow_tenant_language(self):
        wall_clock = time(9, 5)
        self.assertEqual(
            format_time(wall_clock, locale_context("en-US", "America/New_York")),
            "9:05 AM",
        )
        self.assertEqual(
            format_time(wall_clock, locale_context("ja-JP", "Asia/Tokyo")),
            "09:05",
        )
        self.assertEqual(
            format_currency(1234.5, "USD", locale_context("en-US", "UTC")),
            "USD\u00a01,234.50",
        )
        self.assertEqual(
            format_currency(1234.5, "EUR", locale_context("de-DE", "Europe/Berlin")),
            "1.234,50\u00a0EUR",
        )

    def test_currency_code_and_timezone_aware_wall_clock_are_validated(self):
        with self.assertRaisesRegex(ValueError, "ISO 4217"):
            format_currency(10, "usd", locale_context("en-US", "UTC"))
        with self.assertRaisesRegex(ValueError, "timezone-naive"):
            format_time(time(9, 5, tzinfo=timezone.utc),
                        locale_context("en-US", "UTC"))


if __name__ == "__main__":
    unittest.main()
