from __future__ import annotations

import logging
from collections.abc import Sequence

import discord
from discord.ext import commands

from world_cup_bot.data.repositories import (
    AuditLogRepository,
    GuildActiveTournamentConfig,
    TieBreakerAdjudicationRepository,
    TournamentConfigRepository,
)
from world_cup_bot.domain.predictions import TournamentModel
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

    @operator.command(
        name="resolve",
        description="Adjudicate an official standings tie for the active tournament.",
    )
    async def resolve_command(
        self,
        ctx: discord.ApplicationContext,
        scope: str,
        ordered_team_ids: str,
        reason: str,
        group_id: str | None = None,
        config_hash: str | None = None,
    ) -> None:
        if not await self._ensure_operator(ctx):
            return

        normalized_scope = scope.strip().lower()
        if normalized_scope not in {"group", "best_third"}:
            await ctx.respond(
                "Scope must be `group` or `best_third`.",
                ephemeral=True,
            )
            return
        teams = _parse_ordered_team_ids(ordered_team_ids)
        if len(teams) < 2:
            await ctx.respond(
                "Provide at least two ordered team IDs, separated by commas.",
                ephemeral=True,
            )
            return
        if len(set(teams)) != len(teams):
            await ctx.respond("Each team ID can appear only once.", ephemeral=True)
            return
        if not reason.strip():
            await ctx.respond("Provide an official adjudication reason.", ephemeral=True)
            return
        if normalized_scope == "group" and not group_id:
            await ctx.respond("Group adjudications require `group_id`.", ephemeral=True)
            return
        if normalized_scope == "best_third":
            group_id = None

        try:
            tournament = await _select_resolution_tournament(
                self.bot.database.pool,
                config_hash=config_hash,
            )
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return
        try:
            _validate_resolution_teams(
                tournament,
                scope=normalized_scope,
                group_id=group_id,
                team_ids=teams,
            )
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        adjudication = await TieBreakerAdjudicationRepository(
            self.bot.database.pool
        ).save_with_audit(
            tournament_id=tournament.tournament_id,
            config_hash=tournament.config_hash,
            scope=normalized_scope,
            scope_key=group_id.strip().upper() if group_id else "best_third",
            team_ids=teams,
            ordered_team_ids=teams,
            criterion="operator_adjudication",
            reason=reason.strip(),
            actor_user_id=str(ctx.author.id),
        )
        await ctx.respond(
            _resolve_response_message(adjudication),
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


async def _select_resolution_tournament(
    pool: object,
    *,
    config_hash: str | None,
) -> GuildActiveTournamentConfig:
    tournaments = await TournamentConfigRepository(pool).list_active_configs()
    if not tournaments:
        raise ValueError("No active tournament configs are available.")
    grouped: dict[str, GuildActiveTournamentConfig] = {}
    for tournament in tournaments:
        grouped.setdefault(tournament.config_hash, tournament)
    if config_hash:
        for tournament in grouped.values():
            if tournament.config_hash == config_hash:
                return tournament
        raise ValueError("No active tournament config matches that config_hash.")
    if len(grouped) > 1:
        hashes = ", ".join(sorted(grouped)[:5])
        raise ValueError(
            "Multiple active tournament configs are present. "
            f"Pass config_hash. Active hashes: {hashes}"
        )
    return next(iter(grouped.values()))


def _parse_ordered_team_ids(value: str) -> tuple[str, ...]:
    return tuple(
        team_id.strip().upper()
        for team_id in value.split(",")
        if team_id.strip()
    )


def _validate_resolution_teams(
    tournament: GuildActiveTournamentConfig,
    *,
    scope: str,
    group_id: str | None,
    team_ids: tuple[str, ...],
) -> None:
    model = TournamentModel.from_config(tournament.config)
    unknown = sorted(set(team_ids) - set(model.teams_by_id))
    if unknown:
        raise ValueError(f"Unknown team ID(s): {', '.join(unknown)}.")
    if scope == "group":
        group_key = str(group_id).strip().upper()
        group = model.groups_by_id.get(group_key)
        if group is None:
            raise ValueError(f"Unknown group ID: {group_key}.")
        outside_group = sorted(set(team_ids) - set(group.team_ids))
        if outside_group:
            raise ValueError(
                f"Team ID(s) outside group {group_key}: {', '.join(outside_group)}."
            )


def _resolve_response_message(adjudication: object) -> str:
    return (
        "Tie-breaker adjudication saved. "
        f"Scope: {adjudication.scope} {adjudication.scope_key}. "
        f"Order: {', '.join(adjudication.ordered_team_ids)}."
    )
