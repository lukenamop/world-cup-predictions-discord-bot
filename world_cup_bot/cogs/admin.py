from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Mapping

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
from world_cup_bot.ui.discord_formatting import discord_datetime


DEFAULT_PRIVACY_DEFAULTS = {"share_full_bracket": False}
LOCK_MODE = "full_bracket_lock"
POST_KIND_CHOICES = ["leaderboard", "rules", "lock"]
POST_KIND_SET = set(POST_KIND_CHOICES)
LOCK_POST_NEXT_STEP = "Post the public notice with `/admin post kind: lock`."


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
        description="Configure this server's league channels, privacy, scoring, and lock.",
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
        "share_full_bracket_default",
        bool,
        description="Default full-bracket sharing preference for new users.",
        required=False,
    )
    @discord.option(
        "lock_deadline_utc",
        str,
        description="UTC lock deadline like 2026-06-11T18:00:00Z.",
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
        share_full_bracket_default: discord.Option(
            bool,
            "Default full-bracket sharing preference for new users.",
            required=False,
        ) = None,
        lock_deadline_utc: discord.Option(
            str,
            "UTC lock deadline like 2026-06-11T18:00:00Z.",
            required=False,
        ) = None,
    ) -> None:
        if not await self._ensure_admin(ctx):
            return

        guild_id = _guild_id(ctx)
        existing = await GuildSettingsRepository(self.bot.database.pool).get(guild_id)
        try:
            configured_timezone = (
                existing.timezone if existing else self.bot.settings.default_timezone
            )
            lock_deadline_utc = _resolve_lock_deadline(
                existing=existing,
                lock_deadline_utc=lock_deadline_utc,
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
            scoring_rules=(
                existing.scoring_rules
                if existing and existing.scoring_rules
                else _default_scoring_rules()
            ),
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
                title="Prediction League Setup Saved",
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
        "share_full_bracket_default",
        bool,
        description="Default full-bracket sharing preference for new users.",
        required=False,
    )
    @discord.option(
        "lock_deadline_utc",
        str,
        description="UTC lock deadline like 2026-06-11T18:00:00Z.",
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
        share_full_bracket_default: discord.Option(
            bool,
            "Default full-bracket sharing preference for new users.",
            required=False,
        ) = None,
        lock_deadline_utc: discord.Option(
            str,
            "UTC lock deadline like 2026-06-11T18:00:00Z.",
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
            share_full_bracket_default=share_full_bracket_default,
            lock_deadline_utc=lock_deadline_utc,
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
            tournament = await TournamentConfigRepository(
                self.bot.database.pool
            ).get_active_config(guild_id)
            await ctx.respond(
                embed=_setup_embed(
                    settings=existing,
                    title="Prediction League Configuration",
                    tournament=tournament,
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
            configured_timezone = baseline.timezone
            lock_deadline_utc = _resolve_lock_deadline(
                existing=baseline,
                lock_deadline_utc=lock_deadline_utc,
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
            announcement_channel_id=(
                _channel_id(announcement_channel) or baseline.announcement_channel_id
            ),
            leaderboard_channel_id=(
                _channel_id(leaderboard_channel) or baseline.leaderboard_channel_id
            ),
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
                title="Prediction League Configuration Updated",
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
        tournament = await TournamentConfigRepository(
            self.bot.database.pool
        ).get_active_config(guild_id)

        await ctx.respond(
            embed=_status_embed(
                settings=settings,
                tournament=tournament,
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
        tournament = await TournamentConfigRepository(
            self.bot.database.pool
        ).get_active_config(guild_id)
        await ctx.respond(
            (
                "Prediction entry is open. "
                "Lock deadline: "
                f"{_format_lock_deadline(settings.lock_deadline_utc, tournament=tournament)}. "
                f"{LOCK_POST_NEXT_STEP}"
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
        await ctx.respond(
            (
                "Prediction entry is closed. "
                f"{LOCK_POST_NEXT_STEP}"
            ),
            ephemeral=True,
        )

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
        tournament = await TournamentConfigRepository(
            self.bot.database.pool
        ).get_active_config(guild_id)
        await ctx.respond(
            "Prediction lock deadline: "
            f"{_format_lock_deadline(settings.lock_deadline_utc, tournament=tournament)}. "
            f"{LOCK_POST_NEXT_STEP}",
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
        choices=["leaderboard", "rules", "lock"],
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
            choices=POST_KIND_CHOICES,
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
        if normalized not in POST_KIND_SET:
            await ctx.respond(
                "Post kind must be one of: leaderboard, rules, lock.",
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
        tournament = await TournamentConfigRepository(
            self.bot.database.pool
        ).get_active_config(guild_id)
        if kind == "rules":
            return _rules_embed(settings=settings, tournament=tournament)
        if kind == "lock":
            return _lock_embed(settings=settings, tournament=tournament)
        raise ValueError("Post kind must be one of: leaderboard, rules, lock.")

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


def _resolve_lock_deadline(
    *,
    existing: GuildSettings | None,
    lock_deadline_utc: str | None,
    clear_lock_deadline: bool,
) -> datetime | None:
    if clear_lock_deadline:
        return None
    if lock_deadline_utc:
        return _parse_utc_datetime(lock_deadline_utc)
    return existing.lock_deadline_utc if existing else None


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
    rules = (
        _default_scoring_rules()
        if use_default_scoring
        else asdict(ScoringRules.from_mapping(baseline))
    )
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
    share_full_bracket_default: bool | None,
    lock_deadline_utc: str | None,
    clear_lock_deadline: bool,
    use_default_scoring: bool,
    scoring_values: tuple[int | None, ...],
) -> bool:
    return any(
        (
            announcement_channel is not None,
            leaderboard_channel is not None,
            share_full_bracket_default is not None,
            bool(lock_deadline_utc),
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
        value=_format_lock_deadline_for_discord(
            settings.lock_deadline_utc,
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
            "When the league is ready, run `/admin open`. Then run "
            "`/admin post kind: rules`, and `/admin post kind: lock`."
        ),
        inline=False,
    )
    return embed


def _format_channel(channel_id: str | None) -> str:
    return f"<#{channel_id}>" if channel_id else "Not configured"


def _format_lock_deadline_for_discord(
    deadline: datetime | None,
    *,
    tournament: object | None = None,
) -> str:
    if deadline is None:
        first_kickoff = _first_kickoff_utc(tournament)
        if first_kickoff is None:
            return "Auto-locks at first tournament kickoff"
        return "Auto-locks at first kickoff\n" + discord_datetime(first_kickoff)
    return discord_datetime(deadline)


def _format_scoring_rules(rules: ScoringRules) -> str:
    return (
        "Group stage points: "
        f"Group winner {_format_points(rules.group_winner)}, "
        f"group runner-up {_format_points(rules.group_runner_up)}, "
        "advancing third-place team "
        f"{_format_points(rules.group_third_place_qualifier)}\n"
        "Knockout advancement points: "
        "Awarded if a predicted team reaches the specified round, even if it gets "
        "there by a different path.\n"
        f"Ro32 {_format_points(rules.round_of_32_advancement)}, "
        f"Ro16 {_format_points(rules.round_of_16_advancement)}, "
        f"QF {_format_points(rules.quarter_final_advancement)}, "
        f"SF {_format_points(rules.semi_final_advancement)}, "
        f"F {_format_points(rules.final_advancement)}\n"
        "Exact placement points: "
        f"Champion {_format_points(rules.champion)}, "
        f"runner-up {_format_points(rules.runner_up)}, "
        f"third-place {_format_points(rules.third_place_winner)}"
    )


def _format_points(value: int) -> str:
    return f"+{value}"


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
    command_sync_status: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="World Cup League Status",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Tournament",
        value=(
            f"{tournament.tournament_name}\n"
            f"Config `{tournament.tournament_id}`, version `{tournament.config_hash[:12]}`"
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
            _format_lock_deadline_for_discord(
                settings.lock_deadline_utc,
                tournament=tournament,
            )
            if settings
            else "First tournament kickoff"
        ),
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
        title="League Rules",
        description=(
            "Pick teams, not scores. Your full bracket locks before the first group "
            "stage match."
        ),
        color=discord.Color.blurple(),
    )
    if tournament is not None:
        embed.add_field(name="Tournament", value=tournament.tournament_name, inline=False)
    embed.add_field(
        name="Bracket visibility",
        value=(
            "Full brackets are public by default. Use `/preferences` to change yours."
            if settings and _share_full_bracket_default(settings.privacy_defaults)
            else "Full brackets are private by default. Use `/preferences` to share yours."
        ),
        inline=False,
    )
    embed.add_field(
        name="Group stage points",
        value=(
            f"Group winner {_format_points(rules.group_winner)}, "
            f"group runner-up {_format_points(rules.group_runner_up)}, "
            "advancing third-place team "
            f"{_format_points(rules.group_third_place_qualifier)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Knockout advancement points",
        value=(
            "Awarded if a predicted team reaches the specified round, even if it "
            "gets there by a different path.\n"
            f"Ro32 {_format_points(rules.round_of_32_advancement)}, "
            f"Ro16 {_format_points(rules.round_of_16_advancement)}, "
            f"QF {_format_points(rules.quarter_final_advancement)}, "
            f"SF {_format_points(rules.semi_final_advancement)}, "
            f"F {_format_points(rules.final_advancement)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Exact placement points",
        value=(
            f"Champion {_format_points(rules.champion)}, "
            f"runner-up {_format_points(rules.runner_up)}, "
            f"third-place {_format_points(rules.third_place_winner)}"
        ),
        inline=False,
    )
    return embed


def _lock_embed(*, settings: object, tournament: object | None = None) -> discord.Embed:
    predictions_open = bool(settings and settings.predictions_open)
    embed = discord.Embed(
        title="Prediction Lock",
        description=(
            "World Cup predictions are open. Submit or edit your bracket before the lock."
            if predictions_open
            else "Prediction entry is currently closed. Watch this channel for updates."
        ),
        color=discord.Color.gold(),
    )
    if tournament is not None:
        embed.add_field(name="Tournament", value=tournament.tournament_name, inline=False)
    embed.add_field(
        name="Status",
        value="Open" if predictions_open else "Closed",
        inline=True,
    )
    embed.add_field(
        name="Deadline",
        value=(
            _format_lock_deadline_for_discord(
                settings.lock_deadline_utc,
                tournament=tournament,
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


def _format_lock_deadline(
    deadline: datetime | None,
    *,
    tournament: object | None = None,
) -> str:
    if deadline is None:
        first_kickoff = _first_kickoff_utc(tournament)
        if first_kickoff is None:
            return "first tournament kickoff"
        return discord_datetime(first_kickoff)
    return discord_datetime(deadline)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(AdminCog(bot))
