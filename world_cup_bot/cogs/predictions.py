from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import discord
from discord.ext import commands

from world_cup_bot.domain.predictions import (
    Group,
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
    undo_last_prediction_step,
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
from world_cup_bot.ui.discord_formatting import discord_datetime
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
    @discord.option(
        "user",
        discord.Member,
        description="Member whose public prediction summary to show.",
        required=False,
    )
    async def prediction_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(
            discord.Member,
            "Member whose public prediction summary to show.",
            required=False,
        ) = None,
    ) -> None:
        snapshot = await self._snapshot_or_respond(ctx, user)
        if snapshot is None:
            return
        await ctx.respond(embed=_prediction_summary_embed(snapshot), ephemeral=True)

    @discord.slash_command(name="groups", description="Render a user's group prediction sheet.")
    @discord.option(
        "user",
        discord.Member,
        description="Member whose group prediction sheet to render.",
        required=False,
    )
    async def groups_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(
            discord.Member,
            "Member whose group prediction sheet to render.",
            required=False,
        ) = None,
    ) -> None:
        snapshot = await self._snapshot_or_respond(ctx, user)
        if snapshot is None:
            return
        if not snapshot.can_view_full_prediction:
            await ctx.respond(_private_prediction_message(snapshot), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        actual_data = await self._actual_data_or_respond(ctx, snapshot)
        if actual_data is None:
            return
        png = render_groups_png(group_sheet_render_model(snapshot, actual_data))
        await ctx.respond(
            embed=_prediction_summary_embed(snapshot),
            file=_discord_file(png, f"groups-{snapshot.target_user_id}.png"),
            ephemeral=True,
        )

    @discord.slash_command(name="bracket", description="Render a user's knockout bracket.")
    @discord.option(
        "user",
        discord.Member,
        description="Member whose knockout bracket to render.",
        required=False,
    )
    async def bracket_command(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(
            discord.Member,
            "Member whose knockout bracket to render.",
            required=False,
        ) = None,
    ) -> None:
        snapshot = await self._snapshot_or_respond(ctx, user)
        if snapshot is None:
            return
        if not snapshot.can_view_full_prediction:
            await ctx.respond(_private_prediction_message(snapshot), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        actual_data = await self._actual_data_or_respond(ctx, snapshot)
        if actual_data is None:
            return
        png = render_bracket_png(bracket_render_model(snapshot, actual_data))
        await ctx.respond(
            embed=_prediction_summary_embed(snapshot),
            file=_discord_file(png, f"bracket-{snapshot.target_user_id}.png"),
            ephemeral=True,
        )

    @discord.slash_command(name="preferences", description="Manage prediction sharing preferences.")
    @discord.option(
        "share_full_bracket",
        bool,
        description="Whether other members can view your full bracket and group images.",
        required=False,
    )
    async def preferences_command(
        self,
        ctx: discord.ApplicationContext,
        share_full_bracket: discord.Option(
            bool,
            "Whether other members can view your full bracket and group images.",
            required=False,
        ) = None,
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

    async def _actual_data_or_respond(
        self,
        ctx: discord.ApplicationContext,
        snapshot: PredictionSnapshot,
    ) -> dict[str, Any] | None:
        service = PredictionViewService(self.bot.database.pool)
        try:
            return await service.actual_data(
                guild_id=snapshot.guild_id,
                tournament_config_id=snapshot.entry.tournament_config_id,
                model=snapshot.model,
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
        self.pending_values: list[str] = []
        self.review_group_id: str | None = None
        self.finished = False
        self.cancelled = False
        self._refresh_items()

    def build_embed(self) -> discord.Embed:
        if self.cancelled:
            return self._cancelled_embed()

        model = self.session.model
        review_group = self._review_group()
        step = next_prediction_step(model, self.session.data)
        progress = prediction_progress(model, self.session.data)
        embed = discord.Embed(
            title=f"{review_group.label} Complete" if review_group else step.title,
            description=(
                "Review this group ranking, then continue."
                if review_group
                else step.description
            ),
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

        if review_group:
            embed.add_field(
                name="Current group ranking",
                value=_format_group_ranking(model, self.session.data, review_group.id),
                inline=False,
            )
        elif step.kind == "group_pick" and step.group_id:
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
        if not review_group and step.kind != "submit" and self.pending_values:
            embed.add_field(
                name="Pending selection",
                value=_format_pending_selection(step, self.pending_values),
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
        if self.finished:
            return
        review_group = self._review_group()
        step = next_prediction_step(self.session.model, self.session.data)
        progress = prediction_progress(self.session.model, self.session.data)
        if not review_group and step.kind != "submit":
            select = discord.ui.Select(
                placeholder=step.title[:100],
                min_values=step.min_values,
                max_values=step.max_values,
                row=0,
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

        previous_button = discord.ui.Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            disabled=progress.completed <= 0,
            row=1,
        )
        previous_button.callback = self._previous_callback
        self.add_item(previous_button)

        if review_group:
            next_group_button = discord.ui.Button(
                label="Next group" if step.kind == "group_pick" else "Next",
                style=discord.ButtonStyle.primary,
                row=1,
            )
            next_group_button.callback = self._next_group_callback
            self.add_item(next_group_button)

            reset_group_button = discord.ui.Button(
                label="Reset group",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            reset_group_button.callback = self._reset_group_callback
            self.add_item(reset_group_button)
        elif step.kind not in {"group_pick", "submit"}:
            next_button = discord.ui.Button(
                label="Next",
                style=discord.ButtonStyle.primary,
                disabled=not self.pending_values,
                row=1,
            )
            next_button.callback = self._next_callback
            self.add_item(next_button)

        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        cancel_button.callback = self._cancel_entry_callback
        self.add_item(cancel_button)

        if step.kind == "submit":
            submit_button = discord.ui.Button(
                label="Confirm submission",
                style=discord.ButtonStyle.success,
                row=1,
            )
            submit_button.callback = self._submit_callback
            self.add_item(submit_button)

    async def _select_callback(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        step = next_prediction_step(self.session.model, self.session.data)
        try:
            self._stage_or_apply_selection(
                step,
                [str(value) for value in select.values],
            )
        except PredictionValidationError as exc:
            self.notice = str(exc)
        await self._edit(interaction)

    async def _next_group_callback(self, interaction: discord.Interaction) -> None:
        self.review_group_id = None
        self.notice = None
        await self._edit(interaction)

    async def _reset_group_callback(self, interaction: discord.Interaction) -> None:
        group = self._review_group()
        if group is None:
            self.notice = "There is no completed group to reset."
            await self._edit(interaction)
            return

        try:
            for _ in range(len(self._group_ranking(group.id))):
                self.session.data = undo_last_prediction_step(
                    self.session.model,
                    self.session.data,
                )
            self.pending_values = []
            self.review_group_id = None
            self.notice = f"{group.label} reset. Rank it again."
        except PredictionValidationError as exc:
            self.notice = str(exc)
        await self._edit(interaction)

    async def _next_callback(self, interaction: discord.Interaction) -> None:
        if not self.pending_values:
            self.notice = "Choose an option before moving forward."
            await self._edit(interaction)
            return
        step = next_prediction_step(self.session.model, self.session.data)
        try:
            self.session.data = self._apply_step(step, self.pending_values)
            self.pending_values = []
            self.notice = "Selection recorded."
        except PredictionValidationError as exc:
            self.notice = str(exc)
        await self._edit(interaction)

    async def _previous_callback(self, interaction: discord.Interaction) -> None:
        self.pending_values = []
        self.review_group_id = None
        try:
            self.session.data = undo_last_prediction_step(
                self.session.model,
                self.session.data,
            )
            self.notice = "Previous selection removed. Continue from here."
        except PredictionValidationError as exc:
            self.notice = str(exc)
        await self._edit(interaction)

    async def _cancel_entry_callback(self, interaction: discord.Interaction) -> None:
        self.pending_values = []
        self.review_group_id = None
        self.notice = "Prediction entry cancelled. No changes were submitted."
        self.finished = True
        self.cancelled = True
        self.clear_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        self.stop()

    async def _submit_callback(self, interaction: discord.Interaction) -> None:
        try:
            await self.session.submit()
            self.notice = "Prediction submitted."
            self.finished = True
            self.clear_items()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            self.stop()
        except (PredictionValidationError, PredictionServiceError) as exc:
            self.notice = str(exc)
            await self._edit(interaction)

    async def _edit(self, interaction: discord.Interaction) -> None:
        self._refresh_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    def _cancelled_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Prediction Entry Cancelled",
            description="No changes were submitted.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Tournament",
            value=self.session.model.name,
            inline=True,
        )
        embed.add_field(
            name="Next step",
            value="Run `/predict` again to start over.",
            inline=False,
        )
        return embed

    def _stage_or_apply_selection(self, step: PredictionStep, values: list[str]) -> None:
        if step.kind == "group_pick":
            self.session.data = self._apply_step(step, values)
            self.pending_values = []
            if step.group_id and self._is_group_complete(step.group_id):
                self.review_group_id = step.group_id
                self.notice = "Group ranking complete."
            else:
                self.review_group_id = None
                self.notice = "Selection recorded."
            return

        self.review_group_id = None
        self.pending_values = values
        self.notice = "Selection ready. Press Next to record it."

    def _review_group(self) -> Group | None:
        if self.review_group_id is None:
            return None
        group = self.session.model.groups_by_id.get(self.review_group_id)
        if group is None or not self._is_group_complete(group.id):
            self.review_group_id = None
            return None
        return group

    def _is_group_complete(self, group_id: str) -> bool:
        return len(self._group_ranking(group_id)) >= len(
            self.session.model.groups_by_id[group_id].team_ids
        )

    def _group_ranking(self, group_id: str) -> list[str]:
        rankings = self.session.data.get("group_rankings", {})
        ranking = rankings.get(group_id, []) if isinstance(rankings, dict) else []
        return [str(team_id) for team_id in ranking]

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


def _format_pending_selection(step: PredictionStep, values: list[str]) -> str:
    teams = {team.id: team.short_name for team in step.options}
    selected = [teams.get(value, value) for value in values]
    return "\n".join(selected)[:1024] or "No pending selection."


def _format_deadline(deadline: datetime | None) -> str:
    if deadline is None:
        return "First kickoff"
    return discord_datetime(deadline)


def _display_name(author: object) -> str:
    return str(getattr(author, "display_name", None) or getattr(author, "name", author))


def _prediction_summary_embed(snapshot: PredictionSnapshot) -> discord.Embed:
    embed = discord.Embed(
        title=f"{snapshot.display_name}'s Prediction",
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
            discord_datetime(snapshot.lock_deadline_utc)
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
