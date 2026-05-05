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
                "The bot is online. Admins can import tournament data and open "
                "prediction entry; members can build private drafts and submit "
                "full brackets before lock."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Status", value="Prediction entry ready", inline=True)
        embed.add_field(
            name="Privacy",
            value="Prediction flows are private.",
            inline=True,
        )
        embed.add_field(
            name="Commands",
            value="`/predict`, `/edit`, `/admin status`, `/admin import`, `/admin open`, `/admin close`, `/admin lock`",
            inline=False,
        )
        await ctx.respond(embed=embed, ephemeral=True)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(FoundationCog(bot))
