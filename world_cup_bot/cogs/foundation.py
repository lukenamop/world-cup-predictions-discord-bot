from __future__ import annotations

import discord
from discord.ext import commands


class FoundationCog(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(name="help", description="Show bot status and help.")
    async def help_command(self, ctx: discord.ApplicationContext) -> None:
        embed = discord.Embed(
            title="World Cup predictions",
            description=(
                "The bot foundation is online. Prediction entry, tournament import, "
                "leaderboards, and scoring arrive in the next milestones."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Status", value="Foundation ready", inline=True)
        embed.add_field(
            name="Privacy",
            value="Prediction flows will be private.",
            inline=True,
        )
        await ctx.respond(embed=embed, ephemeral=True)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(FoundationCog(bot))
