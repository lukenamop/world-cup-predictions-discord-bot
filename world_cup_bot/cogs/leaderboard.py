from __future__ import annotations

import discord
from discord.ext import commands

from world_cup_bot.domain.predictions import ROUND_LABELS
from world_cup_bot.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardServiceError,
    RankedScore,
)
from world_cup_bot.ui.discord_formatting import (
    discord_datetime,
    discord_timestamp,
    escape_discord_text,
)

PAGE_SIZE = 25
SNAPSHOT_FOOTER = "Use `/leaderboard` to browse the full standings."
EMBED_FIELD_LIMIT = PAGE_SIZE


class LeaderboardCog(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(name="leaderboard", description="Show current league rankings.")
    @discord.option(
        "page",
        int,
        description="Leaderboard page number to open.",
        min_value=1,
    )
    async def leaderboard_command(
        self,
        ctx: discord.ApplicationContext,
        page: discord.Option(
            int,
            "Leaderboard page number to open.",
            min_value=1,
        ) = 1,
    ) -> None:
        if ctx.guild is None:
            await ctx.respond("Leaderboards can only be used in a server.", ephemeral=True)
            return

        try:
            ranked_scores = await LeaderboardService(self.bot.database.pool).top_scores(
                guild_id=str(ctx.guild.id),
                limit=500,
            )
        except LeaderboardServiceError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        if not ranked_scores:
            await ctx.respond(
                "No submitted predictions are available yet.",
                ephemeral=True,
            )
            return

        view = LeaderboardPageView(
            ranked_scores,
            page=max(1, page),
            requester_user_id=str(ctx.author.id),
        )
        await ctx.respond(embed=view.embed(), view=view, ephemeral=True)

    @discord.slash_command(name="rank", description="Show a user's current rank.")
    @discord.option(
        "user",
        discord.Member,
        description="Member whose current rank to show.",
        required=False,
    )
    async def rank_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(
            discord.Member,
            "Member whose current rank to show.",
            required=False,
        ) = None,
    ) -> None:
        if ctx.guild is None:
            await ctx.respond("Ranks can only be used in a server.", ephemeral=True)
            return

        target = user or ctx.author
        try:
            ranked = await LeaderboardService(self.bot.database.pool).user_score(
                guild_id=str(ctx.guild.id),
                user_id=str(target.id),
            )
        except LeaderboardServiceError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        if ranked is None:
            await ctx.respond(
                "That user has not submitted a prediction yet.",
                ephemeral=True,
            )
            return

        await ctx.respond(embed=_rank_embed(ranked), ephemeral=True)

    @discord.slash_command(name="points", description="Show a user's point breakdown.")
    @discord.option(
        "user",
        discord.Member,
        description="Member whose point breakdown to show.",
        required=False,
    )
    async def points_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(
            discord.Member,
            "Member whose point breakdown to show.",
            required=False,
        ) = None,
    ) -> None:
        if ctx.guild is None:
            await ctx.respond("Points can only be used in a server.", ephemeral=True)
            return

        target = user or ctx.author
        try:
            ranked = await LeaderboardService(self.bot.database.pool).user_score(
                guild_id=str(ctx.guild.id),
                user_id=str(target.id),
            )
        except LeaderboardServiceError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        if ranked is None:
            await ctx.respond(
                "That user has not submitted a prediction yet.",
                ephemeral=True,
            )
            return

        await ctx.respond(embed=_points_embed(ranked), ephemeral=True)


class LeaderboardPageView(discord.ui.View):
    def __init__(
        self,
        ranked_scores: list[RankedScore],
        *,
        page: int,
        requester_user_id: str,
    ) -> None:
        super().__init__(timeout=10 * 60)
        self.ranked_scores = ranked_scores
        self.requester_user_id = requester_user_id
        self.page = min(page, self.page_count)
        self._refresh_items()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.ranked_scores) + PAGE_SIZE - 1) // PAGE_SIZE)

    def embed(self) -> discord.Embed:
        return leaderboard_embed(self.ranked_scores, page=self.page)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.requester_user_id:
            return True
        await interaction.response.send_message(
            "Only the person who opened this leaderboard can page it.",
            ephemeral=True,
        )
        return False

    def _refresh_items(self) -> None:
        self.clear_items()
        previous_button = discord.ui.Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 1,
        )
        previous_button.callback = self._previous
        self.add_item(previous_button)

        next_button = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self.page_count,
        )
        next_button.callback = self._next
        self.add_item(next_button)

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.page = max(1, self.page - 1)
        self._refresh_items()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(self.page_count, self.page + 1)
        self._refresh_items()
        await interaction.response.edit_message(embed=self.embed(), view=self)


def leaderboard_embed(
    ranked_scores: list[RankedScore],
    *,
    page: int = 1,
    snapshot: bool = False,
) -> discord.Embed:
    page_count = max(1, (len(ranked_scores) + PAGE_SIZE - 1) // PAGE_SIZE)
    safe_page = 1 if snapshot else min(max(1, page), page_count)
    start = (safe_page - 1) * PAGE_SIZE
    rows = ranked_scores[start : start + PAGE_SIZE]
    label = f"Top {len(rows)}" if snapshot else f"Page {safe_page}/{page_count}"
    embed = _leaderboard_embed(_leaderboard_meta(ranked_scores, label), rows)
    if snapshot:
        embed.set_footer(text=SNAPSHOT_FOOTER)
    return embed


def leaderboard_snapshot_embeds(
    ranked_scores: list[RankedScore],
    *,
    full: bool = False,
) -> tuple[discord.Embed, ...]:
    if not full:
        return (leaderboard_embed(ranked_scores, snapshot=True),)

    page_count = max(
        1,
        (len(ranked_scores) + EMBED_FIELD_LIMIT - 1) // EMBED_FIELD_LIMIT,
    )
    embeds = []
    for page in range(1, page_count + 1):
        start = (page - 1) * EMBED_FIELD_LIMIT
        rows = ranked_scores[start : start + EMBED_FIELD_LIMIT]
        label = f"Full standings ({len(ranked_scores)}) · Part {page}/{page_count}"
        embeds.append(_leaderboard_embed(_leaderboard_meta(ranked_scores, label), rows))
    return tuple(embeds)


def _leaderboard_meta(ranked_scores: list[RankedScore], label: str) -> str:
    latest = max((ranked.score.recalculated_at for ranked in ranked_scores), default=None)
    if latest is None:
        return label
    return f"{label}\nLast updated {discord_timestamp(latest, 'R')}"


def _leaderboard_embed(meta: str, rows: list[RankedScore]) -> discord.Embed:
    embed = discord.Embed(
        title="Leaderboard",
        description=meta,
        color=discord.Color.gold(),
    )
    for ranked in rows[:EMBED_FIELD_LIMIT]:
        embed.add_field(
            name=_leaderboard_field_name(ranked),
            value=_leaderboard_field_value(ranked),
            inline=False,
        )
    return embed


def _leaderboard_field_name(ranked: RankedScore) -> str:
    return _truncate(
        f"#{ranked.rank} {escape_discord_text(ranked.score.display_name)}",
        256,
    )


def _leaderboard_field_value(ranked: RankedScore) -> str:
    return _truncate(" · ".join(_leaderboard_detail_parts(ranked)), 1024)


def leaderboard_row_text(ranked: RankedScore) -> str:
    score = ranked.score
    display_name = escape_discord_text(score.display_name)
    details = " - ".join(_leaderboard_detail_parts(ranked))
    return f"#{ranked.rank} {display_name} - {details}"


def _leaderboard_detail_parts(ranked: RankedScore) -> list[str]:
    score = ranked.score
    champion = escape_discord_text(ranked.champion_team_name or "Unavailable")
    return [
        f"{score.total_points} pts",
        f"Groups: {score.group_points}",
        f"Knockout: {score.knockout_points}",
        f"Champion: {champion}",
    ]


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _rank_embed(ranked: RankedScore) -> discord.Embed:
    score = ranked.score
    embed = discord.Embed(
        title=f"Rank #{ranked.rank}: {escape_discord_text(score.display_name)}",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Total", value=str(score.total_points), inline=True)
    embed.add_field(name="Groups", value=str(score.group_points), inline=True)
    embed.add_field(name="Knockout", value=str(score.knockout_points), inline=True)
    embed.add_field(
        name="Updated",
        value=discord_datetime(score.recalculated_at),
        inline=False,
    )
    return embed


def _points_embed(ranked: RankedScore) -> discord.Embed:
    score = ranked.score
    groups = score.breakdown.get("groups", {})
    knockout = score.breakdown.get("knockout", {})
    placements = knockout.get("placements", {}) if isinstance(knockout, dict) else {}
    embed = _rank_embed(ranked)
    embed.title = f"Point Breakdown: {escape_discord_text(score.display_name)}"
    embed.description = None
    embed.add_field(
        name="Third-place hits",
        value=", ".join(groups.get("third_place_qualifier_hits", [])) or "None yet",
        inline=False,
    )
    embed.add_field(
        name="Advancement",
        value=_advancement_summary(knockout.get("advancement", [])),
        inline=False,
    )
    embed.add_field(
        name="Placements",
        value=_placement_summary(placements),
        inline=False,
    )
    return embed


def _advancement_summary(rows: object) -> str:
    if not isinstance(rows, list):
        return "None yet"
    lines = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        round_name = str(row.get("round") or "")
        label = ROUND_LABELS.get(round_name, round_name.replace("_", " ").title())
        hits = row.get("hits")
        hit_count = len(hits) if isinstance(hits, list) else 0
        team_label = "team" if hit_count == 1 else "teams"
        lines.append(f"{label}: {row.get('points', 0)} pts from {hit_count} {team_label}")
    return "\n".join(lines) or "None yet"


def _placement_summary(placements: object) -> str:
    if not isinstance(placements, dict):
        return "None yet"
    return (
        f"Third place: {placements.get('third_place_points', 0)} pts\n"
        f"Champion: {placements.get('champion_points', 0)} pts\n"
        f"Runner-up: {placements.get('runner_up_points', 0)} pts"
    )


def setup(bot: discord.Bot) -> None:
    bot.add_cog(LeaderboardCog(bot))
