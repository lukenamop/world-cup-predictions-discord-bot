from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import discord
from discord.ext import commands

from world_cup_bot.data.repositories import (
    AuditLogRepository,
    GuildSettingsRepository,
    ResultRepository,
    TournamentConfigRepository,
)
from world_cup_bot.domain.scoring import ScoringRules
from world_cup_bot.services.export_service import ExportService, ExportServiceError
from world_cup_bot.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardServiceError,
)
from world_cup_bot.services.result_sync_service import (
    ResultSyncService,
    ResultSyncServiceError,
)
from world_cup_bot.services.tournament_import import (
    DEFAULT_TOURNAMENT_PATH,
    TournamentImportError,
    load_tournament_config,
)


class AdminCog(commands.Cog):
    admin = discord.SlashCommandGroup(
        "admin",
        "Admin league setup commands.",
        default_member_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @admin.command(name="status", description="Show this server's setup status.")
    async def status_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        settings = await GuildSettingsRepository(self.bot.database.pool).get(guild_id)
        tournament = await TournamentConfigRepository(self.bot.database.pool).get_active(
            guild_id
        )

        await ctx.respond(
            embed=_status_embed(
                settings=settings,
                tournament=tournament,
                default_timezone=self.bot.settings.default_timezone,
                default_provider=self.bot.settings.live_results_provider,
                command_sync_status=self.bot.command_sync_status,
            ),
            ephemeral=True,
        )

    @admin.command(name="open", description="Open prediction entry for this server.")
    async def open_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        settings = await GuildSettingsRepository(
            self.bot.database.pool
        ).set_predictions_open_with_audit(
            guild_id=guild_id,
            timezone=self.bot.settings.default_timezone,
            live_results_provider=self.bot.settings.live_results_provider,
            predictions_open=True,
            actor_user_id=str(ctx.author.id),
            action="predictions_opened",
            details={"predictions_open": True},
        )
        await ctx.respond(
            (
                "Prediction entry is open. "
                f"Lock deadline: {_format_lock_deadline(settings.lock_deadline_utc)}."
            ),
            ephemeral=True,
        )

    @admin.command(name="close", description="Close prediction entry without changing the lock.")
    async def close_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        await GuildSettingsRepository(
            self.bot.database.pool
        ).set_predictions_open_with_audit(
            guild_id=guild_id,
            timezone=self.bot.settings.default_timezone,
            live_results_provider=self.bot.settings.live_results_provider,
            predictions_open=False,
            actor_user_id=str(ctx.author.id),
            action="predictions_closed",
            details={"predictions_open": False},
        )
        await ctx.respond("Prediction entry is closed.", ephemeral=True)

    @admin.command(
        name="lock",
        description="Set or clear the UTC full-bracket lock deadline.",
    )
    async def lock_command(
        self,
        ctx: discord.ApplicationContext,
        deadline_utc: str | None = None,
        clear: bool = False,
    ) -> None:
        if not await self._ensure_admin(ctx):
            return

        if clear:
            parsed_deadline = None
        else:
            if not deadline_utc:
                await ctx.respond(
                    "Pass `deadline_utc` as an ISO-8601 UTC timestamp, or set `clear:True`.",
                    ephemeral=True,
                )
                return
            try:
                parsed_deadline = _parse_utc_datetime(deadline_utc)
            except ValueError as exc:
                await ctx.respond(str(exc), ephemeral=True)
                return

        guild_id = _guild_id(ctx)
        settings = await GuildSettingsRepository(
            self.bot.database.pool
        ).set_lock_deadline_with_audit(
            guild_id=guild_id,
            timezone=self.bot.settings.default_timezone,
            live_results_provider=self.bot.settings.live_results_provider,
            lock_deadline_utc=parsed_deadline,
            actor_user_id=str(ctx.author.id),
            action="prediction_lock_updated",
            details={
                "lock_deadline_utc": (
                    parsed_deadline.isoformat() if parsed_deadline else None
                )
            },
        )
        await ctx.respond(
            f"Prediction lock deadline: {_format_lock_deadline(settings.lock_deadline_utc)}.",
            ephemeral=True,
        )

    @admin.command(
        name="import",
        description="Validate and import a tournament config from config/.",
    )
    async def import_command(
        self,
        ctx: discord.ApplicationContext,
        path: str = str(DEFAULT_TOURNAMENT_PATH),
        validate_only: bool = False,
    ) -> None:
        if not await self._ensure_admin(ctx):
            return

        try:
            imported = load_tournament_config(path, project_root=Path.cwd())
        except TournamentImportError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        if not imported.validation.valid:
            embed = discord.Embed(
                title="Tournament import failed validation",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="File",
                value=f"`{imported.path}`",
                inline=False,
            )
            embed.add_field(
                name="Problems",
                value=_format_problem_list(imported.validation.errors),
                inline=False,
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        summary = imported.validation.summary
        if summary is None:
            await ctx.respond("Tournament validation did not return a summary.", ephemeral=True)
            return

        if validate_only:
            embed = _success_embed(
                title="Tournament config is valid",
                path=imported.path,
                config_hash=imported.config_hash,
                summary=summary,
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        status = await TournamentConfigRepository(self.bot.database.pool).save_active_import(
            guild_id=_guild_id(ctx),
            imported_by_user_id=str(ctx.author.id),
            summary=summary,
            config_hash=imported.config_hash,
            config=dict(imported.config),
        )

        embed = _success_embed(
            title="Tournament imported",
            path=imported.path,
            config_hash=status.config_hash,
            summary=summary,
        )
        await ctx.respond(embed=embed, ephemeral=True)

    @admin.command(
        name="sync",
        description="Show live result sync status or trigger a manual sync.",
    )
    async def sync_command(
        self,
        ctx: discord.ApplicationContext,
        run: bool = False,
    ) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        if not run:
            latest = await ResultRepository(self.bot.database.pool).latest_sync_run(
                guild_id=guild_id
            )
            if latest is None:
                await ctx.respond("No result sync has run for this server yet.", ephemeral=True)
                return
            embed = discord.Embed(
                title="Result sync status",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Provider", value=latest.provider, inline=True)
            embed.add_field(name="Status", value=latest.status, inline=True)
            embed.add_field(
                name="Finished",
                value=(
                    f"{latest.finished_at:%Y-%m-%d %H:%M UTC}"
                    if latest.finished_at
                    else "Still running"
                ),
                inline=True,
            )
            embed.add_field(
                name="Matches",
                value=(
                    f"Fetched {latest.fetched_match_count}, "
                    f"applied {latest.applied_match_count}, "
                    f"warnings {latest.warning_count}"
                ),
                inline=False,
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        sync_service = ResultSyncService(
            self.bot.database.pool,
            provider_name=self.bot.settings.live_results_provider,
            api_key=self.bot.settings.live_results_api_key,
        )
        try:
            summary = await sync_service.sync_guild(guild_id=guild_id)
            recalculation = await LeaderboardService(self.bot.database.pool).recalculate(
                guild_id=guild_id
            )
            await AuditLogRepository(self.bot.database.pool).insert(
                guild_id=guild_id,
                actor_user_id=str(ctx.author.id),
                action="manual_result_sync",
                details={
                    "sync_run_id": summary.sync_run.id,
                    "tournament_config_id": summary.sync_run.tournament_config_id,
                    "provider": summary.sync_run.provider,
                    "fetched_match_count": summary.fetched_match_count,
                    "applied_match_count": summary.applied_match_count,
                    "skipped_match_count": summary.skipped_match_count,
                    "scored_prediction_count": recalculation.scored_prediction_count,
                    "scoring_version": recalculation.scoring_version,
                },
            )
        except (ResultSyncServiceError, LeaderboardServiceError) as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        await ctx.respond(
            (
                "Result sync complete. "
                f"Fetched {summary.fetched_match_count}, applied {summary.applied_match_count}, "
                f"skipped {summary.skipped_match_count}. "
                f"Recalculated {recalculation.scored_prediction_count} submitted prediction(s) "
                f"with scoring {recalculation.scoring_version}."
            ),
            ephemeral=True,
        )

    @admin.command(name="recalc", description="Recalculate scores from stored results.")
    async def recalc_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_admin(ctx):
            return

        await ctx.defer(ephemeral=True)
        try:
            summary = await LeaderboardService(self.bot.database.pool).recalculate(
                guild_id=_guild_id(ctx)
            )
            await AuditLogRepository(self.bot.database.pool).insert(
                guild_id=_guild_id(ctx),
                actor_user_id=str(ctx.author.id),
                action="scores_recalculated",
                details={
                    "tournament_config_id": summary.tournament_config_id,
                    "scored_prediction_count": summary.scored_prediction_count,
                    "result_count": summary.result_count,
                    "scoring_version": summary.scoring_version,
                    "recalculated_at": summary.recalculated_at.isoformat(),
                },
            )
        except LeaderboardServiceError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        await ctx.respond(
            (
                f"Recalculated {summary.scored_prediction_count} submitted prediction(s) "
                f"from {summary.result_count} stored result(s) "
                f"with scoring {summary.scoring_version}."
            ),
            ephemeral=True,
        )

    @admin.command(name="post", description="Post a league announcement snapshot.")
    async def post_command(
        self,
        ctx: discord.ApplicationContext,
        kind: str = "leaderboard",
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        destination = channel or ctx.channel
        if destination is None or not hasattr(destination, "send"):
            await ctx.respond("Pick a text channel for the announcement.", ephemeral=True)
            return

        normalized = kind.strip().lower()
        try:
            embed = await self._announcement_embed(guild_id, normalized)
        except (LeaderboardServiceError, ValueError) as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        await destination.send(embed=embed)
        await AuditLogRepository(self.bot.database.pool).insert(
            guild_id=guild_id,
            actor_user_id=str(ctx.author.id),
            action="announcement_posted",
            details={
                "kind": normalized,
                "channel_id": str(getattr(destination, "id", "")),
            },
        )
        await ctx.respond(f"Posted `{normalized}` to {destination.mention}.", ephemeral=True)

    @admin.command(name="export", description="Export submitted predictions as JSON.")
    async def export_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_admin(ctx):
            return

        await ctx.defer(ephemeral=True)
        guild_id = _guild_id(ctx)
        try:
            filename, content = await ExportService(self.bot.database.pool).prediction_export(
                guild_id=guild_id,
            )
        except ExportServiceError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        await AuditLogRepository(self.bot.database.pool).insert(
            guild_id=guild_id,
            actor_user_id=str(ctx.author.id),
            action="predictions_exported",
            details={"filename": filename},
        )
        await ctx.respond(
            "Prediction export ready.",
            file=discord.File(fp=BytesIO(content), filename=filename),
            ephemeral=True,
        )

    @admin.command(name="backup", description="Create a JSON backup for this league.")
    async def backup_command(self, ctx: discord.ApplicationContext) -> None:
        if not await self._ensure_admin(ctx):
            return

        await ctx.defer(ephemeral=True)
        guild_id = _guild_id(ctx)
        try:
            filename, content = await ExportService(self.bot.database.pool).backup(
                guild_id=guild_id,
            )
        except ExportServiceError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        await AuditLogRepository(self.bot.database.pool).insert(
            guild_id=guild_id,
            actor_user_id=str(ctx.author.id),
            action="league_backup_created",
            details={"filename": filename},
        )
        await ctx.respond(
            "Backup ready.",
            file=discord.File(fp=BytesIO(content), filename=filename),
            ephemeral=True,
        )

    async def _announcement_embed(self, guild_id: str, kind: str) -> discord.Embed:
        if kind == "leaderboard":
            from world_cup_bot.cogs.leaderboard import leaderboard_embed

            scores = await LeaderboardService(self.bot.database.pool).top_scores(
                guild_id=guild_id,
                limit=10,
            )
            if not scores:
                raise ValueError("No leaderboard scores are available yet.")
            return leaderboard_embed(scores)

        settings = await GuildSettingsRepository(self.bot.database.pool).get(guild_id)
        tournament = await TournamentConfigRepository(self.bot.database.pool).get_active(
            guild_id
        )
        if kind == "rules":
            return _rules_embed(settings=settings, tournament=tournament)
        if kind == "lock":
            return _lock_embed(settings=settings)
        if kind == "status":
            return _status_embed(
                settings=settings,
                tournament=tournament,
                default_timezone=self.bot.settings.default_timezone,
                default_provider=self.bot.settings.live_results_provider,
                command_sync_status=self.bot.command_sync_status,
            )
        raise ValueError("Post kind must be one of: leaderboard, rules, lock, status.")

    async def _ensure_admin(self, ctx: discord.ApplicationContext) -> bool:
        if ctx.guild is None:
            await ctx.respond("Admin commands can only be used in a server.", ephemeral=True)
            return False

        # Discord enforces default_member_permissions and guild command overrides
        # before invoking this callback.
        return True


def _guild_id(ctx: discord.ApplicationContext) -> str:
    if ctx.guild is None:
        raise RuntimeError("Admin command used without a guild")
    return str(ctx.guild.id)


def _success_embed(
    *,
    title: str,
    path: Path,
    config_hash: str,
    summary: object,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.green())
    embed.add_field(name="File", value=f"`{path}`", inline=False)
    embed.add_field(name="Tournament", value=f"{summary.name}\n`{summary.tournament_id}`", inline=False)
    embed.add_field(
        name="Imported data",
        value=(
            f"{summary.team_count} teams, {summary.group_count} groups, "
            f"{summary.fixture_count} fixtures, "
            f"{summary.opening_knockout_matches} Round of 32 matches"
        ),
        inline=False,
    )
    embed.add_field(
        name="Third-place allocation",
        value=f"{summary.third_place_rule_count} rules from `{summary.source_version}`",
        inline=False,
    )
    embed.add_field(name="Hash", value=f"`{config_hash[:12]}`", inline=True)
    return embed


def _status_embed(
    *,
    settings: object,
    tournament: object,
    default_timezone: str,
    default_provider: str,
    command_sync_status: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="World Cup league status",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Tournament",
        value=(
            f"{tournament.tournament_name}\n"
            f"`{tournament.tournament_id}` / schema `{tournament.schema_version}`"
            if tournament is not None
            else "Not imported"
        ),
        inline=False,
    )
    embed.add_field(
        name="Data",
        value=(
            f"Hash `{tournament.config_hash[:12]}`\n"
            f"Imported {tournament.imported_at:%Y-%m-%d %H:%M UTC}"
            if tournament is not None
            else "Run `/admin import` with a complete tournament JSON file."
        ),
        inline=False,
    )
    embed.add_field(
        name="Predictions",
        value="Open" if settings and settings.predictions_open else "Closed",
        inline=True,
    )
    embed.add_field(
        name="Lock deadline",
        value=(
            f"{settings.lock_deadline_utc:%Y-%m-%d %H:%M UTC}"
            if settings and settings.lock_deadline_utc
            else "First tournament kickoff"
        ),
        inline=True,
    )
    embed.add_field(
        name="Timezone",
        value=settings.timezone if settings else default_timezone,
        inline=True,
    )
    embed.add_field(
        name="Live provider",
        value=settings.live_results_provider if settings else default_provider,
        inline=True,
    )
    embed.add_field(
        name="Slash commands",
        value=command_sync_status,
        inline=True,
    )
    return embed


def _rules_embed(*, settings: object, tournament: object) -> discord.Embed:
    rules = ScoringRules.from_mapping(settings.scoring_rules if settings else None)
    embed = discord.Embed(
        title="League rules",
        description=(
            "Predictions are score-agnostic and lock as a full bracket. "
            "Knockout scoring gives team-advancement credit even if the path differs."
        ),
        color=discord.Color.blurple(),
    )
    if tournament is not None:
        embed.add_field(name="Tournament", value=tournament.tournament_name, inline=False)
    embed.add_field(
        name="Group stage",
        value=(
            f"Winner {rules.group_winner}, runner-up {rules.group_runner_up}, "
            f"third-place qualifier {rules.group_third_place_qualifier}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Knockout advancement",
        value=(
            f"R32 {rules.round_of_32_advancement}, "
            f"R16 {rules.round_of_16_advancement}, "
            f"QF {rules.quarter_final_advancement}, "
            f"SF {rules.semi_final_advancement}, "
            f"Final {rules.final_advancement}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Placements",
        value=(
            f"Third place {rules.third_place_winner}, "
            f"champion {rules.champion}, runner-up {rules.runner_up}"
        ),
        inline=False,
    )
    return embed


def _lock_embed(*, settings: object) -> discord.Embed:
    embed = discord.Embed(title="Prediction lock", color=discord.Color.gold())
    embed.add_field(
        name="Status",
        value="Open" if settings and settings.predictions_open else "Closed",
        inline=True,
    )
    embed.add_field(
        name="Deadline",
        value=(
            f"{settings.lock_deadline_utc:%Y-%m-%d %H:%M UTC}"
            if settings and settings.lock_deadline_utc
            else "First tournament kickoff"
        ),
        inline=True,
    )
    return embed


def _format_problem_list(problems: tuple[str, ...]) -> str:
    visible = problems[:8]
    lines = [f"- {problem}" for problem in visible]
    if len(problems) > len(visible):
        lines.append(f"- ... and {len(problems) - len(visible)} more")
    output = "\n".join(lines)
    return output[:1024]


def _parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized.removesuffix("Z") + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("deadline_utc must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("deadline_utc must include a UTC offset.")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError("deadline_utc must be a UTC timestamp.")
    return parsed.astimezone(timezone.utc)


def _format_lock_deadline(deadline: datetime | None) -> str:
    if deadline is None:
        return "first tournament kickoff"
    return f"{deadline:%Y-%m-%d %H:%M UTC}"


def setup(bot: discord.Bot) -> None:
    bot.add_cog(AdminCog(bot))
