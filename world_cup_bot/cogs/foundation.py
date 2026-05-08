from __future__ import annotations

import discord
from discord.ext import commands

from world_cup_bot.cogs.admin import _rules_embed
from world_cup_bot.data.repositories import GuildSettingsRepository, TournamentConfigRepository


class FoundationCog(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(name="help", description="Show bot status and help.")
    async def help_command(self, ctx: discord.ApplicationContext) -> None:
        embed = discord.Embed(
            title="World Cup Predictions",
            description=(
                "The bot is online. Admins can configure a league and open "
                "prediction entry; members can complete private sessions and submit "
                "full brackets before lock."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Status",
            value="Use `/admin setup` to configure a league.",
            inline=True,
        )
        embed.add_field(
            name="Entry",
            value="Prediction entry uses private sessions.",
            inline=True,
        )
        embed.add_field(
            name="Issues",
            value=(
                "If something needs developer/operator attention, reach out to "
                "@lukenamop on Discord or email lukenamop@gmail.com."
            ),
            inline=False,
        )
        await ctx.respond(embed=embed, ephemeral=True)

    @discord.slash_command(name="rules", description="Show league scoring and lock rules.")
    async def rules_command(self, ctx: discord.ApplicationContext) -> None:
        if ctx.guild is None:
            await ctx.respond("Rules can only be used in a server.", ephemeral=True)
            return

        guild_id = str(ctx.guild.id)
        settings = await GuildSettingsRepository(self.bot.database.pool).get(guild_id)
        tournament = await TournamentConfigRepository(self.bot.database.pool).get_active(
            guild_id
        )
        await ctx.respond(
            embed=_rules_embed(settings=settings, tournament=tournament),
            ephemeral=True,
        )


def setup(bot: discord.Bot) -> None:
    bot.add_cog(FoundationCog(bot))
