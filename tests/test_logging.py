from __future__ import annotations

import contextlib
import io
import logging
import unittest

from world_cup_bot.logging import configure_logging


class LoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        logging.getLogger().handlers.clear()

    def test_configure_logging_suppresses_pynacl_voice_warning(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            configure_logging("INFO", "development")
            logging.getLogger("discord.client").warning(
                "PyNaCl is not installed, voice will NOT be supported"
            )

        self.assertEqual(output.getvalue(), "")

    def test_configure_logging_keeps_other_discord_warnings(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            configure_logging("INFO", "development")
            logging.getLogger("discord.client").warning("Something else happened")

        self.assertIn("Something else happened", output.getvalue())

