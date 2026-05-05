from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands

from world_cup_bot.data.repositories import (
    GuildSettingsRepository,
    TournamentConfigRepository,
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
            value=(
                "Open" if settings and settings.predictions_open else "Closed"
            ),
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
            value=settings.timezone if settings else self.bot.settings.default_timezone,
            inline=True,
        )
        embed.add_field(
            name="Live provider",
            value=(
                settings.live_results_provider
                if settings
                else self.bot.settings.live_results_provider
            ),
            inline=True,
        )
        embed.add_field(
            name="Slash commands",
            value=self.bot.command_sync_status,
            inline=True,
        )

        await ctx.respond(embed=embed, ephemeral=True)

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
