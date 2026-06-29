from __future__ import annotations

import logging
from collections.abc import Sequence

import discord
from discord.ext import commands

from world_cup_bot.data.repositories import (
    AuditLogRepository,
    GuildActiveTournamentConfig,
    TieBreakerAdjudicationRepository,
    TournamentDataResetRepository,
    TournamentDataResetSummary,
    TournamentConfigRepository,
)
from world_cup_bot.domain.predictions import TournamentModel
from world_cup_bot.jobs.result_sync import (
    ResultSyncFailure,
    ResultSyncJobReport,
    seed_sample_results_all_active_guilds,
    sync_all_active_guilds,
)
from world_cup_bot.services.sample_predictions import (
    FAKE_PREDICTION_USERS,
    SamplePredictionSeedError,
    SamplePredictionSeedService,
    SamplePredictionSeedSummary,
)
from world_cup_bot.services.sample_results import SAMPLE_RESULTS_PROVIDER
from world_cup_bot.services.prediction_view_service import (
    PredictionSnapshot,
    PredictionViewService,
    PredictionViewServiceError,
    bracket_render_model,
    public_prediction_lines,
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
        name="seed-sample",
        description="Seed sample official results through the Round of 16.",
    )
    async def seed_sample_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_operator(ctx):
            return

        await ctx.defer(ephemeral=True)
        report = await seed_sample_results_all_active_guilds(self.bot)
        audit_log = AuditLogRepository(self.bot.database.pool)
        for summary in report.summaries:
            await audit_log.insert(
                guild_id=summary.sync_run.guild_id,
                actor_user_id=str(ctx.author.id),
                action="operator_sample_results_seeded",
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
            _sample_seed_response_message(report),
            ephemeral=True,
        )

    @operator.command(
        name="seed-predictions",
        description="Seed 3 randomized fake predictions into a target guild.",
    )
    @discord.option(
        "guild_id",
        str,
        description="Discord guild ID to receive the fake predictions.",
    )
    async def seed_predictions_command(
        self,
        ctx: discord.ApplicationContext,
        guild_id: discord.Option(
            str,
            "Discord guild ID to receive the fake predictions.",
        ),
    ) -> None:
        if not await self._ensure_operator(ctx):
            return

        normalized_guild_id = guild_id.strip()
        if not normalized_guild_id:
            await ctx.respond("Provide a target guild ID.", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        try:
            summary = await SamplePredictionSeedService(
                self.bot.database.pool
            ).seed_fake_predictions(
                guild_id=normalized_guild_id,
            )
        except SamplePredictionSeedError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        await AuditLogRepository(self.bot.database.pool).insert(
            guild_id=normalized_guild_id,
            actor_user_id=str(ctx.author.id),
            action="operator_sample_predictions_seeded",
            details={
                "tournament_config_id": summary.tournament_config_id,
                "seeded_user_ids": [
                    prediction.user_id
                    for prediction in summary.seeded_predictions
                ],
                "recalculated_score_count": summary.recalculated_score_count,
                "recalculation_error": summary.recalculation_error,
            },
        )
        await ctx.respond(
            _sample_predictions_response_message(summary),
            ephemeral=True,
        )

    @operator.command(
        name="sample-bracket",
        description="Render one sample predictor bracket for a target guild.",
    )
    @discord.option(
        "guild_id",
        str,
        description="Discord guild ID that has the sample prediction.",
    )
    @discord.option(
        "predictor",
        str,
        description="Sample predictor slot to render.",
        choices=["1", "2", "3"],
    )
    async def sample_bracket_command(
        self,
        ctx: discord.ApplicationContext,
        guild_id: discord.Option(
            str,
            "Discord guild ID that has the sample prediction.",
        ),
        predictor: discord.Option(
            str,
            "Sample predictor slot to render.",
            choices=["1", "2", "3"],
        ),
    ) -> None:
        if not await self._ensure_operator(ctx):
            return

        normalized_guild_id = guild_id.strip()
        if not normalized_guild_id:
            await ctx.respond("Provide a target guild ID.", ephemeral=True)
            return

        user_id, display_name = _sample_predictor_user(predictor)
        await ctx.defer(ephemeral=True)
        service = PredictionViewService(self.bot.database.pool)
        try:
            snapshot = await service.snapshot(
                guild_id=normalized_guild_id,
                target_user_id=user_id,
                viewer_user_id=str(ctx.author.id),
            )
            actual_data = await service.actual_data(
                guild_id=normalized_guild_id,
                tournament_config_id=snapshot.entry.tournament_config_id,
                model=snapshot.model,
                tournament_id=snapshot.tournament_id,
                config_hash=snapshot.config_hash,
            )
        except PredictionViewServiceError as exc:
            await ctx.respond(
                (
                    f"{exc} Run `/operator seed-predictions "
                    f"guild_id:{normalized_guild_id}` first if this sample "
                    "predictor has not been created."
                ),
                ephemeral=True,
            )
            return

        from world_cup_bot.ui.image_renderer import render_bracket_png

        png = render_bracket_png(bracket_render_model(snapshot, actual_data))
        filename = f"sample-bracket-{display_name.lower().replace(' ', '-')}.png"
        await ctx.respond(
            embed=_sample_bracket_embed(snapshot, predictor=predictor),
            file=_discord_file(png, filename),
            ephemeral=True,
        )

    @operator.command(
        name="reset-tournament",
        description="Reset active tournament results and delete all predictions.",
    )
    async def reset_tournament_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_operator(ctx):
            return

        await ctx.respond(
            _reset_warning_message(),
            view=_reset_confirmation_view(
                bot=self.bot,
                actor_user_id=str(ctx.author.id),
            ),
            ephemeral=True,
        )

    @operator.command(
        name="resolve",
        description="Adjudicate an official standings tie for the active tournament.",
    )
    @discord.option(
        "scope",
        str,
        description="Tie scope to adjudicate.",
        choices=["group", "best_third"],
    )
    @discord.option(
        "ordered_team_ids",
        str,
        description="Comma-separated team IDs in official resolved order.",
    )
    @discord.option(
        "reason",
        str,
        description="Official reason or source for the adjudication.",
    )
    @discord.option(
        "group_id",
        str,
        description="Group ID for group-scope adjudications, such as A.",
        required=False,
    )
    @discord.option(
        "config_hash",
        str,
        description="Tournament config hash when multiple active configs exist.",
        required=False,
    )
    async def resolve_command(
        self,
        ctx: discord.ApplicationContext,
        scope: discord.Option(
            str,
            "Tie scope to adjudicate.",
            choices=["group", "best_third"],
        ),
        ordered_team_ids: discord.Option(
            str,
            "Comma-separated team IDs in official resolved order.",
        ),
        reason: discord.Option(
            str,
            "Official reason or source for the adjudication.",
        ),
        group_id: discord.Option(
            str,
            "Group ID for group-scope adjudications, such as A.",
            required=False,
        ) = None,
        config_hash: discord.Option(
            str,
            "Tournament config hash when multiple active configs exist.",
            required=False,
        ) = None,
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


def _sample_seed_response_message(report: ResultSyncJobReport) -> str:
    message = _sync_response_message(report)
    return (
        f"Sample result seed used `{SAMPLE_RESULTS_PROVIDER}` and includes "
        f"completed group, Round of 32, and Round of 16 results. {message}"
    )


def _sample_predictions_response_message(summary: SamplePredictionSeedSummary) -> str:
    users = ", ".join(
        f"{prediction.display_name} (`{prediction.user_id}`, rev {prediction.revision})"
        for prediction in summary.seeded_predictions
    )
    recalc = (
        f" Recalculated {summary.recalculated_score_count} score rows."
        if summary.recalculated_score_count is not None
        else f" Score recalculation was skipped: {summary.recalculation_error}"
    )
    return (
        f"Seeded {len(summary.seeded_predictions)} randomized fake predictions "
        f"for guild `{summary.guild_id}`. Users: {users}.{recalc}"
    )


def _sample_predictor_user(predictor: str) -> tuple[str, str]:
    try:
        index = int(predictor) - 1
    except ValueError as exc:
        raise ValueError("Sample predictor must be 1, 2, or 3.") from exc
    if index < 0 or index >= len(FAKE_PREDICTION_USERS):
        raise ValueError("Sample predictor must be 1, 2, or 3.")
    return FAKE_PREDICTION_USERS[index]


def _sample_bracket_embed(
    snapshot: PredictionSnapshot,
    *,
    predictor: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Sample Predictor {predictor} Bracket",
        description="\n".join(public_prediction_lines(snapshot)),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Guild", value=snapshot.guild_id, inline=True)
    embed.add_field(name="User ID", value=snapshot.target_user_id, inline=True)
    embed.add_field(name="Tournament", value=snapshot.tournament_name, inline=True)
    return embed


def _discord_file(data: bytes, filename: str) -> discord.File:
    from io import BytesIO

    return discord.File(BytesIO(data), filename=filename)


def _reset_warning_message() -> str:
    return (
        "**Irreversible reset warning**\n"
        "This will reset every active tournament config back to a no-results state, "
        "delete all user predictions and prediction history, clear scores, remove "
        "stored sync runs, provider caches, result warnings, and tie-breaker "
        "adjudications. Guild setup and active tournament attachments stay in place."
    )


def _reset_complete_message(summary: TournamentDataResetSummary) -> str:
    return (
        "Tournament data reset complete. "
        f"Guilds: {summary.guild_count}. "
        f"Predictions deleted: {summary.prediction_entry_count}. "
        f"History rows: {summary.prediction_history_count}. "
        f"Scores: {summary.prediction_score_count}. "
        f"Match results: {summary.match_result_count}. "
        f"Sync runs: {summary.sync_run_count}. "
        f"Provider caches: {summary.provider_cache_count}. "
        f"Warnings: {summary.sync_warning_count}. "
        f"Tie-breakers: {summary.tie_breaker_count}."
    )


def _reset_confirmation_view(*, bot: object, actor_user_id: str) -> object:
    view = discord.ui.View(timeout=120)
    confirm = discord.ui.Button(
        label="Reset tournament data",
        style=discord.ButtonStyle.danger,
    )
    cancel = discord.ui.Button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
    )

    async def confirm_callback(interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != actor_user_id:
            await interaction.response.send_message(
                "Only the operator who started this reset can confirm it.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="Resetting active tournament data...",
            view=None,
        )
        summary = await TournamentDataResetRepository(
            bot.database.pool
        ).reset_active_tournament_data(
            actor_user_id=actor_user_id,
        )
        await interaction.followup.send(
            _reset_complete_message(summary),
            ephemeral=True,
        )
        view.stop()

    async def cancel_callback(interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != actor_user_id:
            await interaction.response.send_message(
                "Only the operator who started this reset can cancel it.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="Tournament reset cancelled.",
            view=None,
        )
        view.stop()

    confirm.callback = confirm_callback
    cancel.callback = cancel_callback
    view.add_item(confirm)
    view.add_item(cancel)
    return view


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
