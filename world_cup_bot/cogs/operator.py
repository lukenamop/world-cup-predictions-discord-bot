from __future__ import annotations

import logging
from collections.abc import Sequence

import discord
from discord.ext import commands

from world_cup_bot.data.repositories import AuditLogRepository
from world_cup_bot.jobs.result_sync import (
    ResultSyncFailure,
    ResultSyncJobReport,
    sync_all_active_guilds,
)


LOGGER = logging.getLogger(__name__)


class OperatorCog(commands.Cog):
    operator = discord.SlashCommandGroup(
        "operator",
        "Operator-only maintenance commands.",
    )

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @operator.command(
        name="sync",
        description="Run live result sync globally for all configured guilds.",
    )
    async def sync_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_operator(ctx):
            return

        await ctx.defer(ephemeral=True)
        report = await sync_all_active_guilds(self.bot)
        audit_log = AuditLogRepository(self.bot.database.pool)
        for summary in report.summaries:
            await audit_log.insert(
                guild_id=summary.sync_run.guild_id,
                actor_user_id=str(ctx.author.id),
                action="operator_result_sync",
                details={
                    "sync_run_id": summary.sync_run.id,
                    "tournament_config_id": summary.sync_run.tournament_config_id,
                    "provider": summary.sync_run.provider,
                    "fetched_match_count": summary.fetched_match_count,
                    "applied_match_count": summary.applied_match_count,
                    "skipped_match_count": summary.skipped_match_count,
                    "warning_count": summary.warning_count,
                },
            )
        await ctx.respond(
            _sync_response_message(report),
            ephemeral=True,
        )

    async def _ensure_operator(self, ctx: discord.ApplicationContext) -> bool:
        operator_guild_id = self.bot.settings.operator_guild_id
        if operator_guild_id is None:
            await ctx.respond("Operator guild is not configured.", ephemeral=True)
            return False
        if ctx.guild is None or str(ctx.guild.id) != operator_guild_id:
            await ctx.respond(
                "Operator commands can only be used in the configured operator server.",
                ephemeral=True,
            )
            return False
        user_id = str(ctx.author.id)
        if user_id in self.bot.settings.owner_user_ids:
            return True
        permissions = getattr(ctx.author, "guild_permissions", None)
        if permissions and getattr(permissions, "administrator", False):
            return True
        await ctx.respond(
            "Operator commands require Administrator permission or OWNER_USER_IDS access.",
            ephemeral=True,
        )
        return False


def setup(bot: discord.Bot) -> None:
    operator_guild_id = bot.settings.operator_guild_id
    if operator_guild_id is None:
        LOGGER.info("Operator commands disabled; OPERATOR_GUILD_ID is not configured")
        return
    try:
        guild_ids = [int(operator_guild_id)]
    except ValueError:
        LOGGER.error("Operator commands disabled; invalid OPERATOR_GUILD_ID")
        return
    cog = OperatorCog(bot)
    _set_operator_guild_ids(cog, guild_ids)
    bot.add_cog(cog)


def _set_operator_guild_ids(cog: OperatorCog, guild_ids: list[int]) -> None:
    get_commands = getattr(cog, "get_commands", None)
    commands = get_commands() if get_commands else (OperatorCog.operator,)
    for command in commands:
        if getattr(command, "name", None) == "operator":
            command.guild_ids = guild_ids
            return
    OperatorCog.operator.guild_ids = guild_ids


def _sync_response_message(report: ResultSyncJobReport) -> str:
    summaries = report.summaries
    failures = report.failures
    fetched = report.fetched_match_count
    applied = sum(summary.applied_match_count for summary in summaries)
    skipped = sum(summary.skipped_match_count for summary in summaries)
    warnings = sum(summary.warning_count for summary in summaries)
    status = (
        "Global result sync finished with failures."
        if failures
        else "Global result sync complete."
    )
    message = (
        f"{status} "
        f"Guilds synced: {len(summaries)}. "
        f"Failed: {len(failures)}. "
        f"Fetched {fetched}, applied {applied}, skipped {skipped}, "
        f"warnings {warnings}."
    )
    if failures:
        message += f" Failed guilds: {_format_failed_guilds(failures)}."
    return message


def _format_failed_guilds(failures: Sequence[ResultSyncFailure]) -> str:
    visible = ", ".join(failure.guild_id for failure in failures[:10])
    if len(failures) <= 10:
        return visible
    return f"{visible}, +{len(failures) - 10} more"
