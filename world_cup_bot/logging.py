from __future__ import annotations

import logging
import sys


LOG_FORMAT = (
    "%(asctime)s %(levelname)s "
    "%(name)s env=%(bot_env)s message=%(message)s"
)


class BotContextFilter(logging.Filter):
    def __init__(self, bot_env: str) -> None:
        super().__init__()
        self.bot_env = bot_env

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "bot_env"):
            record.bot_env = self.bot_env
        return True


def configure_logging(log_level: str, bot_env: str) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(BotContextFilter(bot_env))
    root_logger.addHandler(handler)
