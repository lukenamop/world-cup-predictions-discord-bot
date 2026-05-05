from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from world_cup_bot.data.database import Database
from world_cup_bot.jobs.result_sync import sync_all_active_guilds
from world_cup_bot.logging import configure_logging
from world_cup_bot.settings import AppSettings, SettingsError


LOGGER = logging.getLogger(__name__)
COGS = (
    "world_cup_bot.cogs.foundation",
    "world_cup_bot.cogs.admin",
    "world_cup_bot.cogs.predictions",
    "world_cup_bot.cogs.leaderboard",
)


async def run_bot() -> None:
    settings = AppSettings.from_env()
    configure_logging(settings.log_level, settings.bot_env)

    try:
        import discord
        from discord.ext import tasks
    except ImportError as exc:
        raise RuntimeError(
            "Pycord is not installed. Run `pip install -e .` inside the virtualenv."
        ) from exc

    intents = discord.Intents.default()
    bot = discord.Bot(intents=intents)
    bot.settings = settings
    bot.database = Database(settings.database_url)
    bot.command_sync_status = "not attempted"

    for cog in COGS:
        bot.load_extension(cog)

    @tasks.loop(minutes=30)
    async def result_sync_loop() -> None:
        await sync_all_active_guilds(bot)

    @bot.event
    async def on_ready() -> None:
        command_sync_at = None
        if bot.command_sync_status == "not attempted":
            command_sync_at = await _sync_commands(bot)
        await bot.database.record_ready(
            bot_env=settings.bot_env,
            guild_count=len(bot.guilds),
            command_sync_at=command_sync_at,
        )
        LOGGER.info(
            "Bot ready as %s; guild_count=%s slash_command_sync=%s",
            bot.user,
            len(bot.guilds),
            bot.command_sync_status,
        )
        if (
            settings.live_results_api_key
            and not result_sync_loop.is_running()
        ):
            result_sync_loop.start()
            LOGGER.info("Result sync job started; interval_minutes=30")

    @bot.event
    async def on_application_command_error(ctx: Any, error: Exception) -> None:
        LOGGER.error(
            "Application command failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        try:
            await ctx.respond(
                "Something went wrong while handling that command.",
                ephemeral=True,
            )
        except Exception:
            LOGGER.exception("Failed to send application command error response")

    LOGGER.info(
        "Starting bot; database=%s owner_count=%s provider=%s",
        settings.database_log_target,
        len(settings.owner_user_ids),
        settings.live_results_provider,
    )

    await bot.database.connect()
    applied_migrations = await bot.database.apply_migrations()
    await bot.database.record_startup(bot_env=settings.bot_env)
    LOGGER.info("Database ready; applied_migrations=%s", applied_migrations)

    try:
        await bot.start(settings.discord_token)
    finally:
        await bot.database.close()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except SettingsError as exc:
        configure_logging("ERROR", "unknown")
        LOGGER.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc


async def _sync_commands(bot: Any) -> datetime | None:
    try:
        await bot.sync_commands()
    except Exception:
        bot.command_sync_status = "failed"
        LOGGER.exception("Slash command sync failed")
        return None

    bot.command_sync_status = "succeeded"
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    main()
