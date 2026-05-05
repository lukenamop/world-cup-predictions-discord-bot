from __future__ import annotations

import unittest

from world_cup_bot.settings import AppSettings, SettingsError, mask_database_url


class SettingsTests(unittest.TestCase):
    def test_settings_require_token_and_database_url(self) -> None:
        with self.assertRaisesRegex(SettingsError, "DISCORD_TOKEN, DATABASE_URL"):
            AppSettings.from_env({}, require_secrets=True)

    def test_settings_parse_defaults_and_owner_ids(self) -> None:
        settings = AppSettings.from_env(
            {
                "DISCORD_TOKEN": " token ",
                "DATABASE_URL": (
                    "postgresql://world_cup_bot:secret@localhost:5432/world_cup_bot"
                ),
                "OWNER_USER_IDS": "123, 456,,",
            }
        )

        self.assertEqual(settings.discord_token, "token")
        self.assertEqual(settings.bot_env, "development")
        self.assertEqual(settings.log_level, "INFO")
        self.assertEqual(settings.owner_user_ids, frozenset({"123", "456"}))
        self.assertEqual(settings.default_timezone, "America/Indiana/Indianapolis")
        self.assertEqual(settings.live_results_provider, "football_data_org")

    def test_invalid_timezone_fails_clearly(self) -> None:
        with self.assertRaisesRegex(SettingsError, "Invalid DEFAULT_TIMEZONE"):
            AppSettings.from_env(
                {
                    "DISCORD_TOKEN": "token",
                    "DATABASE_URL": "postgresql://world_cup_bot@localhost/world_cup_bot",
                    "DEFAULT_TIMEZONE": "Not/AZone",
                }
            )

    def test_invalid_log_level_fails_clearly(self) -> None:
        with self.assertRaisesRegex(SettingsError, "Invalid LOG_LEVEL"):
            AppSettings.from_env(
                {
                    "DISCORD_TOKEN": "token",
                    "DATABASE_URL": "postgresql://world_cup_bot@localhost/world_cup_bot",
                    "LOG_LEVEL": "chatty",
                }
            )

    def test_database_url_without_database_name_fails_clearly(self) -> None:
        with self.assertRaisesRegex(SettingsError, "missing database name"):
            AppSettings.from_env(
                {
                    "DISCORD_TOKEN": "token",
                    "DATABASE_URL": "postgresql://world_cup_bot:secret@localhost:5432",
                }
            )

    def test_database_url_without_user_fails_clearly(self) -> None:
        with self.assertRaisesRegex(SettingsError, "missing database user"):
            AppSettings.from_env(
                {
                    "DISCORD_TOKEN": "token",
                    "DATABASE_URL": "postgresql://localhost:5432/world_cup_bot",
                }
            )

    def test_database_url_mask_hides_password(self) -> None:
        masked = mask_database_url(
            "postgresql://world_cup_bot:super-secret@db.example.com:5432/world_cup_bot"
        )

        self.assertEqual(
            masked,
            "postgresql://world_cup_bot:***@db.example.com:5432/world_cup_bot",
        )
        self.assertNotIn("super-secret", masked)
