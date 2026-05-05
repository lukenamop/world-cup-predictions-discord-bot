from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from world_cup_bot.data.database import Database
from world_cup_bot.logging import configure_logging
from world_cup_bot.settings import AppSettings, SettingsError


LOGGER = logging.getLogger(__name__)


async def run() -> int:
    settings = AppSettings.from_env()
    configure_logging(settings.log_level, settings.bot_env)

    database = Database(settings.database_url)
    try:
        await database.connect()
        applied_migrations = await database.apply_migrations()
        result = await database.health_check()
        LOGGER.info(
            "Health check passed; database=%s applied_migrations=%s result=%s",
            settings.database_log_target,
            applied_migrations,
            result,
        )
        return 0
    finally:
        await database.close()


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run()))
    except SettingsError as exc:
        configure_logging("ERROR", "unknown")
        LOGGER.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
