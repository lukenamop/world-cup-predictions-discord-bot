from __future__ import annotations

import discord
from discord.ext import commands

from world_cup_bot.domain.predictions import ROUND_LABELS
from world_cup_bot.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardServiceError,
    RankedScore,
    leaderboard_row_text,
)

PAGE_SIZE = 10


class LeaderboardCog(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(name="leaderboard", description="Show current league rankings.")
    async def leaderboard_command(
        self,
        ctx: discord.ApplicationContext,
        page: int = 1,
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
                "No scores are available yet. Ask an admin to run `/admin recalc` after results are stored.",
                ephemeral=True,
            )
            return

        view = LeaderboardPageView(
            ranked_scores,
            page=max(1, page),
            requester_user_id=str(ctx.author.id),
        )
        await ctx.respond(embed=view.embed(), view=view)

    @discord.slash_command(name="rank", description="Show a user's current rank.")
    async def rank_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Member | None = None,
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
                "No score is available for that user yet. Ask an admin to run `/admin recalc` after results are stored.",
                ephemeral=True,
            )
            return

        await ctx.respond(embed=_rank_embed(ranked), ephemeral=True)

    @discord.slash_command(name="points", description="Show a user's point breakdown.")
    async def points_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Member | None = None,
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
                "No score is available for that user yet. Ask an admin to run `/admin recalc` after results are stored.",
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
) -> discord.Embed:
    page_count = max(1, (len(ranked_scores) + PAGE_SIZE - 1) // PAGE_SIZE)
    safe_page = min(max(1, page), page_count)
    start = (safe_page - 1) * PAGE_SIZE
    rows = ranked_scores[start : start + PAGE_SIZE]
    embed = discord.Embed(
        title="Leaderboard",
        description=f"Page {safe_page}/{page_count}",
        color=discord.Color.gold(),
    )
    lines = []
    for ranked in rows:
        lines.append(leaderboard_row_text(ranked))
    embed.add_field(name="Rankings", value="\n".join(lines)[:1024], inline=False)
    latest = max((ranked.score.recalculated_at for ranked in ranked_scores), default=None)
    if latest is not None:
        embed.set_footer(text=f"Last updated {latest:%Y-%m-%d %H:%M UTC}")
    return embed


def _rank_embed(ranked: RankedScore) -> discord.Embed:
    score = ranked.score
    embed = discord.Embed(
        title=f"Rank #{ranked.rank}",
        description=score.display_name,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Total", value=str(score.total_points), inline=True)
    embed.add_field(name="Groups", value=str(score.group_points), inline=True)
    embed.add_field(name="Knockout", value=str(score.knockout_points), inline=True)
    embed.add_field(
        name="Updated",
        value=f"{score.recalculated_at:%Y-%m-%d %H:%M UTC}",
        inline=False,
    )
    return embed


def _points_embed(ranked: RankedScore) -> discord.Embed:
    score = ranked.score
    groups = score.breakdown.get("groups", {})
    knockout = score.breakdown.get("knockout", {})
    placements = knockout.get("placements", {}) if isinstance(knockout, dict) else {}
    embed = _rank_embed(ranked)
    embed.title = f"Point breakdown: {score.display_name}"
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
    embed.add_field(
        name="Scoring version",
        value=str(score.breakdown.get("version") or score.scoring_version),
        inline=True,
    )
    embed.add_field(
        name="Knockout points",
        value=str(knockout.get("points", score.knockout_points)),
        inline=True,
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
        lines.append(f"{label}: {row.get('points', 0)} ({hit_count})")
    return "\n".join(lines) or "None yet"


def _placement_summary(placements: object) -> str:
    if not isinstance(placements, dict):
        return "None yet"
    return (
        f"Third place: {placements.get('third_place_points', 0)}\n"
        f"Champion: {placements.get('champion_points', 0)}\n"
        f"Runner-up: {placements.get('runner_up_points', 0)}"
    )


def setup(bot: discord.Bot) -> None:
    bot.add_cog(LeaderboardCog(bot))
