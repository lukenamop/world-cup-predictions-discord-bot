from __future__ import annotations

import unittest
from datetime import datetime, timezone

from world_cup_bot.ui.discord_formatting import discord_datetime, discord_timestamp


class DiscordFormattingTests(unittest.TestCase):
    def test_discord_timestamp_formats_aware_datetime(self) -> None:
        value = datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc)

        self.assertEqual(discord_timestamp(value, "R"), "<t:1781204400:R>")

    def test_discord_datetime_includes_absolute_and_relative_markers(self) -> None:
        value = datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc)

        self.assertEqual(
            discord_datetime(value),
            "<t:1781204400:F> (<t:1781204400:R>)",
        )

    def test_discord_timestamp_rejects_unknown_styles(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported Discord timestamp style"):
            discord_timestamp(datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc), "x")


if __name__ == "__main__":
    unittest.main()
