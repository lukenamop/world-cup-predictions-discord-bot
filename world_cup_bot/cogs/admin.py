from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands

from world_cup_bot.data.repositories import (
    AuditLogRepository,
    GuildSettings,
    GuildSettingsRepository,
    TournamentConfigRepository,
)
from world_cup_bot.domain.locks import effective_lock_deadline
from world_cup_bot.domain.scoring import ScoringRules
from world_cup_bot.services.export_service import ExportService, ExportServiceError
from world_cup_bot.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardServiceError,
)
from world_cup_bot.services.tournament_import import (
    TournamentImportError,
    load_tournament_config,
)


DEFAULT_PRIVACY_DEFAULTS = {"share_full_bracket": False}
LOCK_MODE = "full_bracket_lock"


@dataclass(frozen=True)
class TournamentEmbedContext:
    tournament_id: str
    tournament_name: str
    config_hash: str
    first_kickoff_utc: datetime | None = None


class AdminCog(commands.Cog):
    admin = discord.SlashCommandGroup(
        "admin",
        "Admin league setup commands.",
        default_member_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @admin.command(
        name="setup",
        description="Configure this server's league channels, timezone, privacy, scoring, and lock.",
    )
    @discord.option(
        "announcement_channel",
        discord.TextChannel,
        description="Text channel for prediction notices and reminders.",
    )
    @discord.option(
        "leaderboard_channel",
        discord.TextChannel,
        description="Text channel for leaderboard posts.",
    )
    @discord.option(
        "timezone_name",
        str,
        description="IANA timezone for local deadlines, such as America/New_York.",
        required=False,
    )
    @discord.option(
        "share_full_bracket_default",
        bool,
        description="Default full-bracket sharing preference for new users.",
        required=False,
    )
    @discord.option(
        "lock_deadline_local",
        str,
        description="Local lock deadline like 2026-06-11 12:00.",
        required=False,
    )
    async def setup_command(
        self,
        ctx: discord.ApplicationContext,
        announcement_channel: discord.Option(
            discord.TextChannel,
            "Text channel for prediction notices and reminders.",
        ),
        leaderboard_channel: discord.Option(
            discord.TextChannel,
            "Text channel for leaderboard posts.",
        ),
        timezone_name: discord.Option(
            str,
            "IANA timezone for local deadlines, such as America/New_York.",
            required=False,
        ) = None,
        share_full_bracket_default: discord.Option(
            bool,
            "Default full-bracket sharing preference for new users.",
            required=False,
        ) = None,
        lock_deadline_local: discord.Option(
            str,
            "Local lock deadline like 2026-06-11 12:00.",
            required=False,
        ) = None,
    ) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        existing = await GuildSettingsRepository(self.bot.database.pool).get(guild_id)
        try:
            configured_timezone = _validate_timezone_name(
                timezone_name or (existing.timezone if existing else self.bot.settings.default_timezone)
            )
            lock_deadline_utc = _resolve_lock_deadline(
                existing=existing,
                timezone_name=configured_timezone,
                lock_deadline_local=lock_deadline_local,
                clear_lock_deadline=False,
            )
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        settings = GuildSettings(
            guild_id=guild_id,
            announcement_channel_id=_channel_id(announcement_channel),
            leaderboard_channel_id=_channel_id(leaderboard_channel),
            timezone=configured_timezone,
            live_results_provider=self.bot.settings.live_results_provider,
            lock_deadline_utc=lock_deadline_utc,
            predictions_open=existing.predictions_open if existing else False,
            scoring_rules=existing.scoring_rules if existing and existing.scoring_rules else _default_scoring_rules(),
            privacy_defaults={
                "share_full_bracket": (
                    bool(share_full_bracket_default)
                    if share_full_bracket_default is not None
                    else (
                        _share_full_bracket_default(existing.privacy_defaults)
                        if existing
                        else False
                    )
                )
            },
            lock_mode=LOCK_MODE,
        )
        saved = await GuildSettingsRepository(
            self.bot.database.pool
        ).save_settings_with_audit(
            settings=settings,
            actor_user_id=str(ctx.author.id),
            action="guild_setup_updated",
            details=_settings_audit_details(saved_settings=settings, existing=existing),
        )
        try:
            tournament = await _ensure_canonical_tournament_config(
                self.bot.database.pool,
                guild_id=guild_id,
                actor_user_id=str(ctx.author.id),
            )
        except TournamentImportError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        await ctx.respond(
            embed=_setup_embed(
                settings=saved,
                title="Prediction league setup saved",
                tournament=tournament,
            ),
            ephemeral=True,
        )

    @admin.command(
        name="config",
        description="View or update this server's league configuration.",
    )
    @discord.option(
        "announcement_channel",
        discord.TextChannel,
        description="Text channel for prediction notices and reminders.",
        required=False,
    )
    @discord.option(
        "leaderboard_channel",
        discord.TextChannel,
        description="Text channel for leaderboard posts.",
        required=False,
    )
    @discord.option(
        "timezone_name",
        str,
        description="IANA timezone for local deadlines, such as America/New_York.",
        required=False,
    )
    @discord.option(
        "share_full_bracket_default",
        bool,
        description="Default full-bracket sharing preference for new users.",
        required=False,
    )
    @discord.option(
        "lock_deadline_local",
        str,
        description="Local lock deadline like 2026-06-11 12:00.",
        required=False,
    )
    @discord.option(
        "clear_lock_deadline",
        bool,
        description="Clear the configured lock deadline.",
    )
    @discord.option(
        "use_default_scoring",
        bool,
        description="Reset scoring values to the MVP defaults.",
    )
    @discord.option(
        "group_winner",
        int,
        description="Points for correctly predicting a group winner.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "group_runner_up",
        int,
        description="Points for correctly predicting a group runner-up.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "group_third_place_qualifier",
        int,
        description="Points for a correct advancing third-place team.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "round_of_32_advancement",
        int,
        description="Points for a correct Round of 32 advancement pick.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "round_of_16_advancement",
        int,
        description="Points for a correct Round of 16 advancement pick.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "quarter_final_advancement",
        int,
        description="Points for a correct quarter-final advancement pick.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "semi_final_advancement",
        int,
        description="Points for a correct semi-final advancement pick.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "final_advancement",
        int,
        description="Points for correctly predicting a finalist.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "third_place_winner",
        int,
        description="Points for correctly predicting the third-place winner.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "champion",
        int,
        description="Points for correctly predicting the champion.",
        min_value=0,
        required=False,
    )
    @discord.option(
        "runner_up",
        int,
        description="Points for correctly predicting the runner-up.",
        min_value=0,
        required=False,
    )
    async def config_command(
        self,
        ctx: discord.ApplicationContext,
        announcement_channel: discord.Option(
            discord.TextChannel,
            "Text channel for prediction notices and reminders.",
            required=False,
        ) = None,
        leaderboard_channel: discord.Option(
            discord.TextChannel,
            "Text channel for leaderboard posts.",
            required=False,
        ) = None,
        timezone_name: discord.Option(
            str,
            "IANA timezone for local deadlines, such as America/New_York.",
            required=False,
        ) = None,
        share_full_bracket_default: discord.Option(
            bool,
            "Default full-bracket sharing preference for new users.",
            required=False,
        ) = None,
        lock_deadline_local: discord.Option(
            str,
            "Local lock deadline like 2026-06-11 12:00.",
            required=False,
        ) = None,
        clear_lock_deadline: discord.Option(
            bool,
            "Clear the configured lock deadline.",
        ) = False,
        use_default_scoring: discord.Option(
            bool,
            "Reset scoring values to the MVP defaults.",
        ) = False,
        group_winner: discord.Option(
            int,
            "Points for correctly predicting a group winner.",
            min_value=0,
            required=False,
        ) = None,
        group_runner_up: discord.Option(
            int,
            "Points for correctly predicting a group runner-up.",
            min_value=0,
            required=False,
        ) = None,
        group_third_place_qualifier: discord.Option(
            int,
            "Points for a correct advancing third-place team.",
            min_value=0,
            required=False,
        ) = None,
        round_of_32_advancement: discord.Option(
            int,
            "Points for a correct Round of 32 advancement pick.",
            min_value=0,
            required=False,
        ) = None,
        round_of_16_advancement: discord.Option(
            int,
            "Points for a correct Round of 16 advancement pick.",
            min_value=0,
            required=False,
        ) = None,
        quarter_final_advancement: discord.Option(
            int,
            "Points for a correct quarter-final advancement pick.",
            min_value=0,
            required=False,
        ) = None,
        semi_final_advancement: discord.Option(
            int,
            "Points for a correct semi-final advancement pick.",
            min_value=0,
            required=False,
        ) = None,
        final_advancement: discord.Option(
            int,
            "Points for correctly predicting a finalist.",
            min_value=0,
            required=False,
        ) = None,
        third_place_winner: discord.Option(
            int,
            "Points for correctly predicting the third-place winner.",
            min_value=0,
            required=False,
        ) = None,
        champion: discord.Option(
            int,
            "Points for correctly predicting the champion.",
            min_value=0,
            required=False,
        ) = None,
        runner_up: discord.Option(
            int,
            "Points for correctly predicting the runner-up.",
            min_value=0,
            required=False,
        ) = None,
    ) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        existing = await GuildSettingsRepository(self.bot.database.pool).get(guild_id)
        scoring_values = _scoring_option_values(
            group_winner=group_winner,
            group_runner_up=group_runner_up,
            group_third_place_qualifier=group_third_place_qualifier,
            round_of_32_advancement=round_of_32_advancement,
            round_of_16_advancement=round_of_16_advancement,
            quarter_final_advancement=quarter_final_advancement,
            semi_final_advancement=semi_final_advancement,
            final_advancement=final_advancement,
            third_place_winner=third_place_winner,
            champion=champion,
            runner_up=runner_up,
        )
        has_updates = _config_has_updates(
            announcement_channel=announcement_channel,
            leaderboard_channel=leaderboard_channel,
            timezone_name=timezone_name,
            share_full_bracket_default=share_full_bracket_default,
            lock_deadline_local=lock_deadline_local,
            clear_lock_deadline=clear_lock_deadline,
            use_default_scoring=use_default_scoring,
            scoring_values=scoring_values,
        )
        if existing is None and not has_updates:
            await ctx.respond(
                "No setup exists yet. Run `/admin setup` to configure this server.",
                ephemeral=True,
            )
            return

        if existing is not None and not has_updates:
            await ctx.respond(
                embed=_setup_embed(
                    settings=existing,
                    title="Prediction league configuration",
                ),
                ephemeral=True,
            )
            return

        baseline = existing or _new_default_settings(
            guild_id=guild_id,
            default_timezone=self.bot.settings.default_timezone,
            live_provider=self.bot.settings.live_results_provider,
        )
        try:
            configured_timezone = _validate_timezone_name(timezone_name or baseline.timezone)
            lock_deadline_utc = _resolve_lock_deadline(
                existing=baseline,
                timezone_name=configured_timezone,
                lock_deadline_local=lock_deadline_local,
                clear_lock_deadline=clear_lock_deadline,
            )
            scoring_rules = _updated_scoring_rules(
                baseline=baseline.scoring_rules,
                use_default_scoring=use_default_scoring,
                group_winner=group_winner,
                group_runner_up=group_runner_up,
                group_third_place_qualifier=group_third_place_qualifier,
                round_of_32_advancement=round_of_32_advancement,
                round_of_16_advancement=round_of_16_advancement,
                quarter_final_advancement=quarter_final_advancement,
                semi_final_advancement=semi_final_advancement,
                final_advancement=final_advancement,
                third_place_winner=third_place_winner,
                champion=champion,
                runner_up=runner_up,
            )
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        settings = GuildSettings(
            guild_id=guild_id,
            announcement_channel_id=_channel_id(announcement_channel) or baseline.announcement_channel_id,
            leaderboard_channel_id=_channel_id(leaderboard_channel) or baseline.leaderboard_channel_id,
            timezone=configured_timezone,
            live_results_provider=self.bot.settings.live_results_provider,
            lock_deadline_utc=lock_deadline_utc,
            predictions_open=baseline.predictions_open,
            scoring_rules=scoring_rules,
            privacy_defaults={
                "share_full_bracket": (
                    bool(share_full_bracket_default)
                    if share_full_bracket_default is not None
                    else _share_full_bracket_default(baseline.privacy_defaults)
                )
            },
            lock_mode=LOCK_MODE,
        )
        saved = await GuildSettingsRepository(
            self.bot.database.pool
        ).save_settings_with_audit(
            settings=settings,
            actor_user_id=str(ctx.author.id),
            action="guild_config_updated",
            details=_settings_audit_details(saved_settings=settings, existing=existing),
        )
        try:
            tournament = await _ensure_canonical_tournament_config(
                self.bot.database.pool,
                guild_id=guild_id,
                actor_user_id=str(ctx.author.id),
            )
        except TournamentImportError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        await ctx.respond(
            embed=_setup_embed(
                settings=saved,
                title="Prediction league configuration updated",
                tournament=tournament,
            ),
            ephemeral=True,
        )

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
    @discord.option(
        "deadline_utc",
        str,
        description="UTC ISO-8601 deadline like 2026-06-11T18:00:00Z.",
        required=False,
    )
    @discord.option(
        "clear",
        bool,
        description="Clear the configured lock deadline.",
    )
    async def lock_command(
        self,
        ctx: discord.ApplicationContext,
        deadline_utc: discord.Option(
            str,
            "UTC ISO-8601 deadline like 2026-06-11T18:00:00Z.",
            required=False,
        ) = None,
        clear: discord.Option(
            bool,
            "Clear the configured lock deadline.",
        ) = False,
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
    @discord.option(
        "kind",
        str,
        description="Snapshot type to post.",
        choices=["leaderboard", "rules", "lock", "status", "reminder"],
    )
    @discord.option(
        "channel",
        discord.TextChannel,
        description="Text channel to post to instead of the configured default.",
        required=False,
    )
    async def post_command(
        self,
        ctx: discord.ApplicationContext,
        kind: discord.Option(
            str,
            "Snapshot type to post.",
            choices=["leaderboard", "rules", "lock", "status", "reminder"],
        ) = "leaderboard",
        channel: discord.Option(
            discord.TextChannel,
            "Text channel to post to instead of the configured default.",
            required=False,
        ) = None,
    ) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        normalized = kind.strip().lower()
        if normalized not in {"leaderboard", "rules", "lock", "status", "reminder"}:
            await ctx.respond(
                "Post kind must be one of: leaderboard, rules, lock, status, reminder.",
                ephemeral=True,
            )
            return

        settings = await GuildSettingsRepository(self.bot.database.pool).get(guild_id)
        destination = channel or _configured_post_channel(
            ctx=ctx,
            settings=settings,
            kind=normalized,
        )
        if destination is None or not hasattr(destination, "send"):
            await ctx.respond(
                (
                    "No configured channel is available for that post. "
                    "Run `/admin setup` or pass `channel:` explicitly."
                ),
                ephemeral=True,
            )
            return

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
        if kind == "reminder":
            return _reminder_embed(settings=settings, tournament=tournament)
        if kind == "status":
            return _status_embed(
                settings=settings,
                tournament=tournament,
                default_timezone=self.bot.settings.default_timezone,
                command_sync_status=self.bot.command_sync_status,
            )
        raise ValueError("Post kind must be one of: leaderboard, rules, lock, status, reminder.")

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


def _new_default_settings(
    *,
    guild_id: str,
    default_timezone: str,
    live_provider: str,
) -> GuildSettings:
    return GuildSettings(
        guild_id=guild_id,
        timezone=default_timezone,
        live_results_provider=live_provider,
        lock_deadline_utc=None,
        predictions_open=False,
        scoring_rules=_default_scoring_rules(),
        privacy_defaults=dict(DEFAULT_PRIVACY_DEFAULTS),
        lock_mode=LOCK_MODE,
    )


async def _ensure_canonical_tournament_config(
    pool: object,
    *,
    guild_id: str,
    actor_user_id: str,
) -> object:
    imported = load_tournament_config(project_root=Path.cwd())
    if not imported.validation.valid:
        raise TournamentImportError(
            "Canonical tournament config failed validation: "
            f"{_format_problem_list(imported.validation.errors)}"
        )
    summary = imported.validation.summary
    if summary is None:
        raise TournamentImportError(
            "Canonical tournament validation did not return a summary."
        )
    saved = await TournamentConfigRepository(pool).save_active_import(
        guild_id=guild_id,
        imported_by_user_id=actor_user_id,
        summary=summary,
        config_hash=imported.config_hash,
        config=dict(imported.config),
    )
    return TournamentEmbedContext(
        tournament_id=saved.tournament_id,
        tournament_name=saved.tournament_name,
        config_hash=saved.config_hash,
        first_kickoff_utc=effective_lock_deadline(
            configured_deadline_utc=None,
            tournament_config=imported.config,
        ),
    )


def _default_scoring_rules() -> dict[str, int]:
    return asdict(ScoringRules())


def _validate_timezone_name(value: str) -> str:
    timezone_name = value.strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            "Timezone must be an IANA timezone name, for example "
            "`America/New_York`, `America/Chicago`, `America/Denver`, "
            "`America/Los_Angeles`, or `UTC`."
        ) from exc
    return timezone_name


def _resolve_lock_deadline(
    *,
    existing: GuildSettings | None,
    timezone_name: str,
    lock_deadline_local: str | None,
    clear_lock_deadline: bool,
) -> datetime | None:
    if clear_lock_deadline:
        return None
    if lock_deadline_local:
        return _parse_local_datetime(lock_deadline_local, timezone_name)
    return existing.lock_deadline_utc if existing else None


def _parse_local_datetime(value: str, timezone_name: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z") or "+" in normalized[10:] or "-" in normalized[10:]:
        raise ValueError(
            "lock_deadline_local should be a local time without a UTC offset, "
            "for example `2026-06-11 12:00`."
        )
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "lock_deadline_local must look like `2026-06-11 12:00` "
            "or `2026-06-11T12:00`."
        ) from exc
    if parsed.tzinfo is not None:
        raise ValueError("lock_deadline_local should not include a timezone offset.")
    return parsed.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)


def _updated_scoring_rules(
    *,
    baseline: dict[str, object],
    use_default_scoring: bool,
    group_winner: int | None,
    group_runner_up: int | None,
    group_third_place_qualifier: int | None,
    round_of_32_advancement: int | None,
    round_of_16_advancement: int | None,
    quarter_final_advancement: int | None,
    semi_final_advancement: int | None,
    final_advancement: int | None,
    third_place_winner: int | None,
    champion: int | None,
    runner_up: int | None,
) -> dict[str, int]:
    rules = _default_scoring_rules() if use_default_scoring else asdict(ScoringRules.from_mapping(baseline))
    updates = {
        "group_winner": group_winner,
        "group_runner_up": group_runner_up,
        "group_third_place_qualifier": group_third_place_qualifier,
        "round_of_32_advancement": round_of_32_advancement,
        "round_of_16_advancement": round_of_16_advancement,
        "quarter_final_advancement": quarter_final_advancement,
        "semi_final_advancement": semi_final_advancement,
        "final_advancement": final_advancement,
        "third_place_winner": third_place_winner,
        "champion": champion,
        "runner_up": runner_up,
    }
    for key, raw_value in updates.items():
        if raw_value is not None:
            rules[key] = _positive_score(key, raw_value)
    return rules


def _positive_score(name: str, value: int) -> int:
    if value < 0:
        raise ValueError(f"{name} must be zero or greater.")
    return value


def _scoring_option_values(**values: int | None) -> tuple[int | None, ...]:
    return tuple(values.values())


def _config_has_updates(
    *,
    announcement_channel: object | None,
    leaderboard_channel: object | None,
    timezone_name: str | None,
    share_full_bracket_default: bool | None,
    lock_deadline_local: str | None,
    clear_lock_deadline: bool,
    use_default_scoring: bool,
    scoring_values: tuple[int | None, ...],
) -> bool:
    return any(
        (
            announcement_channel is not None,
            leaderboard_channel is not None,
            bool(timezone_name),
            share_full_bracket_default is not None,
            bool(lock_deadline_local),
            clear_lock_deadline,
            use_default_scoring,
            any(value is not None for value in scoring_values),
        )
    )


def _share_full_bracket_default(privacy_defaults: dict[str, object]) -> bool:
    return bool(privacy_defaults.get("share_full_bracket", False))


def _settings_audit_details(
    *,
    saved_settings: GuildSettings,
    existing: GuildSettings | None,
) -> dict[str, object]:
    return {
        "before": _settings_snapshot(existing) if existing else None,
        "after": _settings_snapshot(saved_settings),
    }


def _settings_snapshot(settings: GuildSettings) -> dict[str, object]:
    return {
        "announcement_channel_id": settings.announcement_channel_id,
        "leaderboard_channel_id": settings.leaderboard_channel_id,
        "timezone": settings.timezone,
        "privacy_defaults": settings.privacy_defaults,
        "scoring_rules": settings.scoring_rules,
        "lock_mode": settings.lock_mode,
        "lock_deadline_utc": (
            settings.lock_deadline_utc.isoformat()
            if settings.lock_deadline_utc
            else None
        ),
        "predictions_open": settings.predictions_open,
        "live_results_provider": settings.live_results_provider,
    }


def _channel_id(channel: object | None) -> str | None:
    if channel is None:
        return None
    if isinstance(channel, str):
        return channel
    value = getattr(channel, "id", None)
    return str(value) if value is not None else None


def _configured_post_channel(
    *,
    ctx: discord.ApplicationContext,
    settings: GuildSettings | None,
    kind: str,
) -> object | None:
    if settings is None:
        return None
    channel_id = (
        settings.leaderboard_channel_id
        if kind == "leaderboard"
        else settings.announcement_channel_id
    )
    if not channel_id or ctx.guild is None or not hasattr(ctx.guild, "get_channel"):
        return None
    try:
        return ctx.guild.get_channel(int(channel_id))
    except ValueError:
        return None


def _setup_embed(
    *,
    settings: GuildSettings,
    title: str,
    tournament: object | None = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.green())
    embed.add_field(
        name="Announcements",
        value=_format_channel(settings.announcement_channel_id),
        inline=True,
    )
    embed.add_field(
        name="Leaderboard",
        value=_format_channel(settings.leaderboard_channel_id),
        inline=True,
    )
    embed.add_field(name="Timezone", value=settings.timezone, inline=True)
    embed.add_field(
        name="Privacy default",
        value=(
            "Prediction brackets public by default\n"
            "Users can change this with `/preferences`."
            if _share_full_bracket_default(settings.privacy_defaults)
            else (
                "Prediction brackets private by default\n"
                "Users can change this with `/preferences`."
            )
        ),
        inline=False,
    )
    embed.add_field(
        name="Lock deadline",
        value=_format_lock_deadline_with_local(
            settings.lock_deadline_utc,
            settings.timezone,
            tournament=tournament,
        ),
        inline=False,
    )
    embed.add_field(
        name="Scoring defaults",
        value=_format_scoring_rules(ScoringRules.from_mapping(settings.scoring_rules)),
        inline=False,
    )
    if tournament is not None:
        embed.add_field(
            name="Tournament",
            value=(
                f"{tournament.tournament_name}\n"
                f"Config `{tournament.tournament_id}`, version `{tournament.config_hash[:12]}`"
            ),
            inline=False,
        )
    embed.add_field(
        name="Predictions",
        value="Open" if settings.predictions_open else "Closed",
        inline=True,
    )
    embed.add_field(
        name="Next steps",
        value=(
            "Run `/admin post kind: rules`, then `/admin post kind: status`.\n"
            "When the league is ready, run `/admin open`."
        ),
        inline=False,
    )
    return embed


def _format_channel(channel_id: str | None) -> str:
    return f"<#{channel_id}>" if channel_id else "Not configured"


def _format_lock_deadline_with_local(
    deadline: datetime | None,
    timezone_name: str,
    *,
    tournament: object | None = None,
) -> str:
    if deadline is None:
        first_kickoff = _first_kickoff_utc(tournament)
        if first_kickoff is None:
            return "Auto-locks at first tournament kickoff"
        return "Auto-locks at first kickoff\n" + _format_datetime_with_local(
            first_kickoff,
            timezone_name,
        )
    return _format_datetime_with_local(deadline, timezone_name)


def _format_datetime_with_local(deadline: datetime, timezone_name: str) -> str:
    local_deadline = deadline.astimezone(ZoneInfo(timezone_name))
    return (
        f"{local_deadline:%Y-%m-%d %H:%M %Z} "
        f"({deadline:%Y-%m-%d %H:%M UTC})"
    )


def _format_scoring_rules(rules: ScoringRules) -> str:
    return (
        "Group table picks: "
        f"winner {rules.group_winner}, runner-up {rules.group_runner_up}, "
        f"advancing third-place team {rules.group_third_place_qualifier}\n"
        "Knockout advancement: "
        f"Round of 32 {rules.round_of_32_advancement}, "
        f"Round of 16 {rules.round_of_16_advancement}, "
        f"quarter-final {rules.quarter_final_advancement}, "
        f"semi-final {rules.semi_final_advancement}, finalist {rules.final_advancement}\n"
        "Final placements: "
        f"champion {rules.champion}, runner-up {rules.runner_up}, "
        f"third-place match winner {rules.third_place_winner}\n"
        "Knockout points are for teams reaching each stage, not exact bracket slots."
    )


def _first_kickoff_utc(tournament: object | None) -> datetime | None:
    if tournament is None:
        return None
    value = getattr(tournament, "first_kickoff_utc", None)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    config = getattr(tournament, "config", None)
    if isinstance(config, Mapping):
        return effective_lock_deadline(
            configured_deadline_utc=None,
            tournament_config=config,
        )
    return None


def _status_embed(
    *,
    settings: object,
    tournament: object,
    default_timezone: str,
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
            else "Run `/admin setup` to attach the canonical 2026 World Cup data."
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
        name="Announcements",
        value=(
            _format_channel(settings.announcement_channel_id)
            if settings
            else "Not configured"
        ),
        inline=True,
    )
    embed.add_field(
        name="Leaderboard",
        value=(
            _format_channel(settings.leaderboard_channel_id)
            if settings
            else "Not configured"
        ),
        inline=True,
    )
    embed.add_field(
        name="Privacy default",
        value=(
            "Prediction brackets public by default"
            if settings and _share_full_bracket_default(settings.privacy_defaults)
            else "Prediction brackets private by default"
        ),
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


def _reminder_embed(*, settings: object, tournament: object) -> discord.Embed:
    predictions_open = bool(settings and settings.predictions_open)
    embed = discord.Embed(
        title="Prediction reminder",
        description=(
            "World Cup predictions are open. Submit or edit your bracket before the lock."
            if predictions_open
            else "Prediction entry is currently closed. Watch this channel for updates."
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Tournament",
        value=tournament.tournament_name if tournament is not None else "Not configured",
        inline=False,
    )
    embed.add_field(
        name="Status",
        value="Open" if predictions_open else "Closed",
        inline=True,
    )
    embed.add_field(
        name="Deadline",
        value=(
            _format_lock_deadline_with_local(
                settings.lock_deadline_utc,
                settings.timezone,
            )
            if settings
            else "First tournament kickoff"
        ),
        inline=True,
    )
    embed.add_field(
        name="Commands",
        value="Use `/predict` to submit. Use `/edit` to replace a submitted bracket before lock.",
        inline=False,
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
