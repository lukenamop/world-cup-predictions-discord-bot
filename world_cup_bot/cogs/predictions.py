from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import discord
from discord.ext import commands

from world_cup_bot.domain.predictions import (
    PredictionStep,
    PredictionValidationError,
    TournamentModel,
    empty_prediction_data,
    next_prediction_step,
    prediction_progress,
    prediction_summary,
    record_group_pick,
    record_knockout_winner,
    record_third_place_qualifiers,
    restart_prediction_data,
)
from world_cup_bot.services.prediction_service import (
    PredictionService,
    PredictionServiceError,
    PredictionSessionState,
)
from world_cup_bot.services.prediction_view_service import (
    PredictionSnapshot,
    PredictionViewService,
    PredictionViewServiceError,
    bracket_render_model,
    group_sheet_render_model,
    public_prediction_lines,
)
from world_cup_bot.ui.image_renderer import render_bracket_png, render_groups_png


class PredictionsCog(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(name="predict", description="Start your prediction entry.")
    async def predict_command(self, ctx: discord.ApplicationContext) -> None:
        await self._start_prediction(ctx, edit_existing=False)

    @discord.slash_command(name="edit", description="Replace your submitted prediction before lock.")
    async def edit_command(self, ctx: discord.ApplicationContext) -> None:
        await self._start_prediction(ctx, edit_existing=True)

    @discord.slash_command(name="prediction", description="Show a user's prediction summary.")
    async def prediction_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Member | None = None,
    ) -> None:
        snapshot = await self._snapshot_or_respond(ctx, user)
        if snapshot is None:
            return
        await ctx.respond(embed=_prediction_summary_embed(snapshot), ephemeral=True)

    @discord.slash_command(name="groups", description="Render a user's group prediction sheet.")
    async def groups_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Member | None = None,
    ) -> None:
        snapshot = await self._snapshot_or_respond(ctx, user)
        if snapshot is None:
            return
        if not snapshot.can_view_full_prediction:
            await ctx.respond(_private_prediction_message(snapshot), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        service = PredictionViewService(self.bot.database.pool)
        actual_data = await service.actual_data(
            guild_id=snapshot.guild_id,
            tournament_config_id=snapshot.entry.tournament_config_id,
            model=snapshot.model,
        )
        png = render_groups_png(group_sheet_render_model(snapshot, actual_data))
        await ctx.respond(
            embed=_prediction_summary_embed(snapshot),
            file=_discord_file(png, f"groups-{snapshot.target_user_id}.png"),
            ephemeral=True,
        )

    @discord.slash_command(name="bracket", description="Render a user's knockout bracket.")
    async def bracket_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Member | None = None,
    ) -> None:
        snapshot = await self._snapshot_or_respond(ctx, user)
        if snapshot is None:
            return
        if not snapshot.can_view_full_prediction:
            await ctx.respond(_private_prediction_message(snapshot), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        service = PredictionViewService(self.bot.database.pool)
        actual_data = await service.actual_data(
            guild_id=snapshot.guild_id,
            tournament_config_id=snapshot.entry.tournament_config_id,
            model=snapshot.model,
        )
        png = render_bracket_png(bracket_render_model(snapshot, actual_data))
        await ctx.respond(
            embed=_prediction_summary_embed(snapshot),
            file=_discord_file(png, f"bracket-{snapshot.target_user_id}.png"),
            ephemeral=True,
        )

    @discord.slash_command(name="preferences", description="Manage prediction sharing preferences.")
    async def preferences_command(
        self,
        ctx: discord.ApplicationContext,
        share_full_bracket: bool | None = None,
    ) -> None:
        if ctx.guild is None:
            await ctx.respond("Preferences can only be used in a server.", ephemeral=True)
            return

        service = PredictionViewService(self.bot.database.pool)
        if share_full_bracket is None:
            preferences = await service.preferences.get(
                guild_id=str(ctx.guild.id),
                user_id=str(ctx.author.id),
            )
        else:
            preferences = await service.set_share_full_bracket(
                guild_id=str(ctx.guild.id),
                user_id=str(ctx.author.id),
                share_full_bracket=share_full_bracket,
            )

        await ctx.respond(
            (
                "Full bracket sharing is "
                f"{'on' if preferences.share_full_bracket else 'off'}. "
                "Champion, runner-up, and third-place picks remain visible."
            ),
            ephemeral=True,
        )

    async def _snapshot_or_respond(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Member | None,
    ) -> PredictionSnapshot | None:
        if ctx.guild is None:
            await ctx.respond("Prediction views can only be used in a server.", ephemeral=True)
            return None

        target = user or ctx.author
        try:
            return await PredictionViewService(self.bot.database.pool).snapshot(
                guild_id=str(ctx.guild.id),
                target_user_id=str(target.id),
                viewer_user_id=str(ctx.author.id),
            )
        except PredictionViewServiceError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return None

    async def _start_prediction(
        self,
        ctx: discord.ApplicationContext,
        *,
        edit_existing: bool,
    ) -> None:
        if ctx.guild is None:
            await ctx.respond("Predictions can only be used in a server.", ephemeral=True)
            return

        service = PredictionService(self.bot.database.pool)
        try:
            state = await service.start_prediction(
                guild_id=str(ctx.guild.id),
                user_id=str(ctx.author.id),
                edit_existing=edit_existing,
            )
        except PredictionServiceError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        session = PredictionEntrySession(
            service=service,
            state=state,
            user_id=str(ctx.author.id),
            display_name=_display_name(ctx.author),
            data=state.data or empty_prediction_data(),
        )
        view = PredictionEntryView(session)
        await ctx.respond(embed=view.build_embed(), view=view, ephemeral=True)


class PredictionEntrySession:
    def __init__(
        self,
        *,
        service: PredictionService,
        state: PredictionSessionState,
        user_id: str,
        display_name: str,
        data: dict[str, Any],
    ) -> None:
        self.service = service
        self.state = state
        self.user_id = user_id
        self.display_name = display_name
        self.data = data

    @property
    def model(self) -> TournamentModel:
        return self.state.model

    async def submit(self) -> None:
        await self.service.submit(
            state=self.state,
            user_id=self.user_id,
            display_name=self.display_name,
            data=self.data,
        )


class PredictionEntryView(discord.ui.View):
    def __init__(self, session: PredictionEntrySession) -> None:
        super().__init__(timeout=15 * 60)
        self.session = session
        self.notice: str | None = None
        self._refresh_items()

    def build_embed(self) -> discord.Embed:
        model = self.session.model
        step = next_prediction_step(model, self.session.data)
        progress = prediction_progress(model, self.session.data)
        embed = discord.Embed(
            title=step.title,
            description=step.description,
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Tournament",
            value=model.name,
            inline=True,
        )
        embed.add_field(
            name="Progress",
            value=f"{progress.completed}/{progress.total}",
            inline=True,
        )
        embed.add_field(
            name="Lock",
            value=_format_deadline(self.session.state.lock_deadline_utc),
            inline=True,
        )
        if self.notice:
            embed.add_field(name="Status", value=self.notice[:1024], inline=False)

        if step.kind == "group_pick" and step.group_id:
            embed.add_field(
                name="Current group ranking",
                value=_format_group_ranking(model, self.session.data, step.group_id),
                inline=False,
            )
        elif step.kind == "submit":
            embed.add_field(
                name="Prediction summary",
                value=_format_summary(model, self.session.data),
                inline=False,
            )
        else:
            embed.add_field(
                name="Choices",
                value="\n".join(team.short_name for team in step.options)[:1024],
                inline=False,
            )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.session.user_id:
            return True
        await interaction.response.send_message(
            "This private prediction session belongs to someone else.",
            ephemeral=True,
        )
        return False

    def _refresh_items(self) -> None:
        self.clear_items()
        step = next_prediction_step(self.session.model, self.session.data)
        if step.kind != "submit":
            select = discord.ui.Select(
                placeholder=step.title[:100],
                min_values=step.min_values,
                max_values=step.max_values,
                options=[
                    discord.SelectOption(
                        label=team.short_name[:100],
                        value=team.id,
                        description=team.name[:100] if team.name != team.short_name else None,
                    )
                    for team in step.options
                ],
            )
            async def select_callback(interaction: discord.Interaction) -> None:
                await self._select_callback(interaction, select)

            select.callback = select_callback
            self.add_item(select)

        restart_button = discord.ui.Button(
            label="Start over",
            style=discord.ButtonStyle.danger,
        )
        restart_button.callback = self._restart_callback
        self.add_item(restart_button)

        if step.kind == "submit":
            submit_button = discord.ui.Button(
                label="Submit prediction",
                style=discord.ButtonStyle.success,
            )
            submit_button.callback = self._submit_callback
            self.add_item(submit_button)

    async def _select_callback(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        step = next_prediction_step(self.session.model, self.session.data)
        values = [str(value) for value in select.values]
        try:
            self.session.data = self._apply_step(step, values)
            self.notice = "Selection recorded."
        except PredictionValidationError as exc:
            self.notice = str(exc)
        await self._edit(interaction)

    async def _restart_callback(self, interaction: discord.Interaction) -> None:
        self.session.data = restart_prediction_data()
        self.notice = "Prediction entry restarted. Nothing changes until you submit."
        await self._edit(interaction)

    async def _submit_callback(self, interaction: discord.Interaction) -> None:
        try:
            await self.session.submit()
            self.notice = "Prediction submitted."
            self.clear_items()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            self.stop()
        except (PredictionValidationError, PredictionServiceError) as exc:
            self.notice = str(exc)
            await self._edit(interaction)

    async def _edit(self, interaction: discord.Interaction) -> None:
        self._refresh_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    def _apply_step(self, step: PredictionStep, values: list[str]) -> dict[str, Any]:
        if step.kind == "group_pick":
            if not step.group_id or not values:
                raise PredictionValidationError("Pick one team.")
            return record_group_pick(
                self.session.model,
                self.session.data,
                group_id=step.group_id,
                team_id=values[0],
            )
        if step.kind == "third_place":
            return record_third_place_qualifiers(
                self.session.model,
                self.session.data,
                team_ids=values,
            )
        if step.kind == "knockout":
            if not step.round_name or not step.match_id or not values:
                raise PredictionValidationError("Pick one winner.")
            return record_knockout_winner(
                self.session.model,
                self.session.data,
                round_name=step.round_name,
                match_id=step.match_id,
                winner_team_id=values[0],
            )
        raise PredictionValidationError("This step is already complete.")


def _format_group_ranking(
    model: TournamentModel,
    data: dict[str, Any],
    group_id: str,
) -> str:
    rankings = data.get("group_rankings", {})
    ranking = rankings.get(group_id, []) if isinstance(rankings, dict) else []
    if not ranking:
        return "No teams ranked yet."
    return "\n".join(
        f"{index}. {model.team(str(team_id)).short_name}"
        for index, team_id in enumerate(ranking, start=1)
    )


def _format_summary(model: TournamentModel, data: dict[str, Any]) -> str:
    try:
        summary = prediction_summary(model, data)
    except PredictionValidationError:
        return "Finish every step to unlock submission."
    return (
        f"Champion: {model.team(summary.champion_team_id).short_name}\n"
        f"Runner-up: {model.team(summary.runner_up_team_id).short_name}\n"
        f"Third place: {model.team(summary.third_place_team_id).short_name}\n"
        f"Fourth place: {model.team(summary.fourth_place_team_id).short_name}"
    )


def _format_deadline(deadline: datetime | None) -> str:
    if deadline is None:
        return "First kickoff"
    return f"{deadline:%Y-%m-%d %H:%M UTC}"


def _display_name(author: object) -> str:
    return str(getattr(author, "display_name", None) or getattr(author, "name", author))


def _prediction_summary_embed(snapshot: PredictionSnapshot) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.display_name}'s prediction",
        description="\n".join(public_prediction_lines(snapshot)),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Privacy",
        value=(
            "Full bracket shared"
            if snapshot.preferences.share_full_bracket
            else "Full bracket private"
        ),
        inline=True,
    )
    embed.add_field(
        name="Lock",
        value=(
            f"{snapshot.lock_deadline_utc:%Y-%m-%d %H:%M UTC}"
            if snapshot.lock_deadline_utc
            else "Not configured"
        ),
        inline=True,
    )
    if snapshot.score is not None:
        embed.add_field(
            name="Points",
            value=(
                f"{snapshot.score.total_points} total\n"
                f"{snapshot.score.group_points} group / "
                f"{snapshot.score.knockout_points} knockout"
            ),
            inline=False,
        )
    return embed


def _private_prediction_message(snapshot: PredictionSnapshot) -> str:
    return (
        f"{snapshot.display_name} keeps full brackets private. "
        "Use `/prediction` for their visible placement picks."
    )


def _discord_file(content: bytes, filename: str) -> discord.File:
    return discord.File(fp=BytesIO(content), filename=filename)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(PredictionsCog(bot))
