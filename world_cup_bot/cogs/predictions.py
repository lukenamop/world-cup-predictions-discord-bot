from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import discord
from discord.ext import commands

from world_cup_bot.domain.predictions import (
    Group,
    ROUND_LABELS,
    ROUND_ORDER,
    PredictionStep,
    PredictionValidationError,
    RoundMatch,
    TournamentModel,
    empty_prediction_data,
    get_round_matches,
    next_prediction_step,
    prediction_progress,
    prediction_summary,
    predicted_third_place_by_group,
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
    REVIEW_KNOCKOUT_ROUNDS = frozenset(
        {"round_of_32", "round_of_16", "quarter_finals", "semi_finals"}
    )

    def __init__(self, session: PredictionEntrySession) -> None:
        super().__init__(timeout=15 * 60)
        self.session = session
        self.notice: str | None = None
        self.pending_values: list[str] = []
        self.review_group_id: str | None = None
        self.review_round_name: str | None = None
        self.finished = False
        self.cancelled = False
        self._refresh_items()

    def build_embed(self) -> discord.Embed:
        if self.cancelled:
            return self._cancelled_embed()
        if self.finished:
            return self._submitted_embed()

        model = self.session.model
        review_group = self._review_group()
        review_round_name = self._review_round_name()
        step = next_prediction_step(model, self.session.data)
        progress = prediction_progress(model, self.session.data)
        embed = discord.Embed(
            title=_review_title(review_group, review_round_name) or step.title,
            description=(
                "Review this group ranking, then continue."
                if review_group
                else "Review this knockout round, then continue."
                if review_round_name
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
        elif review_round_name:
            _add_knockout_recap_fields(embed, model, self.session.data, review_round_name)
        elif step.kind == "group_pick" and step.group_id:
            embed.add_field(
                name="Current group ranking",
                value=_format_group_ranking(model, self.session.data, step.group_id),
                inline=False,
            )
        elif step.kind == "submit":
            _add_submission_review_fields(embed, model, self.session.data)
        else:
            embed.add_field(
                name="Choices",
                value="\n".join(team.short_name for team in step.options)[:1024],
                inline=False,
            )
        if (
            not review_group
            and not review_round_name
            and step.kind != "submit"
            and self.pending_values
        ):
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
        review_round_name = self._review_round_name()
        step = next_prediction_step(self.session.model, self.session.data)
        progress = prediction_progress(self.session.model, self.session.data)
        if not review_group and not review_round_name and step.kind == "submit":
            edit_select = discord.ui.Select(
                placeholder="Choose where to start editing",
                min_values=1,
                max_values=1,
                row=0,
                options=_edit_section_options(),
            )

            async def edit_select_callback(interaction: discord.Interaction) -> None:
                await self._edit_section_callback(interaction, edit_select)

            edit_select.callback = edit_select_callback
            self.add_item(edit_select)
        elif not review_group and not review_round_name and step.kind != "submit":
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

        if review_group or review_round_name:
            next_review_button = discord.ui.Button(
                label="Next group" if review_group else "Next round",
                style=discord.ButtonStyle.primary,
                row=1,
            )
            next_review_button.callback = self._next_group_callback
            self.add_item(next_review_button)

            reset_button = discord.ui.Button(
                label="Reset group" if review_group else "Reset round",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            reset_button.callback = (
                self._reset_group_callback if review_group else self._reset_round_callback
            )
            self.add_item(reset_button)
        elif step.kind == "third_place":
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
            style=discord.ButtonStyle.danger,
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

    async def _edit_section_callback(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        if not select.values:
            self.notice = "Choose a section to edit."
            await self._edit(interaction)
            return

        try:
            self._jump_to_edit_section(str(select.values[0]))
        except PredictionValidationError as exc:
            self.notice = str(exc)
        await self._edit(interaction)

    async def _next_group_callback(self, interaction: discord.Interaction) -> None:
        self.review_group_id = None
        self.review_round_name = None
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
            self.review_round_name = None
            self.notice = f"{group.label} reset. Rank it again."
        except PredictionValidationError as exc:
            self.notice = str(exc)
        await self._edit(interaction)

    async def _reset_round_callback(self, interaction: discord.Interaction) -> None:
        round_name = self._review_round_name()
        if round_name is None:
            self.notice = "There is no completed knockout round to reset."
            await self._edit(interaction)
            return

        try:
            for _ in range(len(self._round_entries(round_name))):
                self.session.data = undo_last_prediction_step(
                    self.session.model,
                    self.session.data,
                )
            self.pending_values = []
            self.review_group_id = None
            self.review_round_name = None
            self.notice = f"{ROUND_LABELS[round_name]} reset. Pick it again."
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
        self.review_round_name = None
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
        self.review_round_name = None
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

    def _submitted_embed(self) -> discord.Embed:
        progress = prediction_progress(self.session.model, self.session.data)
        embed = discord.Embed(
            title="Prediction Submitted",
            description="Your prediction has been saved.",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Tournament",
            value=self.session.model.name,
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
        _add_submission_review_fields(embed, self.session.model, self.session.data)
        return embed

    def _stage_or_apply_selection(self, step: PredictionStep, values: list[str]) -> None:
        if step.kind == "group_pick":
            self.session.data = self._apply_step(step, values)
            self.pending_values = []
            self.review_round_name = None
            if step.group_id and self._is_group_complete(step.group_id):
                self.review_group_id = step.group_id
                self.notice = "Group ranking complete."
            else:
                self.review_group_id = None
                self.notice = "Selection recorded."
            return

        if step.kind == "knockout":
            self.session.data = self._apply_step(step, values)
            self.pending_values = []
            self.review_group_id = None
            if step.round_name and self._should_review_round(step.round_name):
                self.review_round_name = step.round_name
                self.notice = f"{ROUND_LABELS[step.round_name]} complete."
            else:
                self.review_round_name = None
                self.notice = "Selection recorded."
            return

        self.review_group_id = None
        self.review_round_name = None
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

    def _review_round_name(self) -> str | None:
        round_name = self.review_round_name
        if round_name is None:
            return None
        if not self._should_review_round(round_name):
            self.review_round_name = None
            return None
        return round_name

    def _is_group_complete(self, group_id: str) -> bool:
        return len(self._group_ranking(group_id)) >= len(
            self.session.model.groups_by_id[group_id].team_ids
        )

    def _should_review_round(self, round_name: str) -> bool:
        return (
            round_name in self.REVIEW_KNOCKOUT_ROUNDS
            and self._is_knockout_round_complete(round_name)
        )

    def _is_knockout_round_complete(self, round_name: str) -> bool:
        matches = get_round_matches(self.session.model, self.session.data, round_name)
        return bool(matches) and all(match.winner_team_id for match in matches)

    def _group_ranking(self, group_id: str) -> list[str]:
        rankings = self.session.data.get("group_rankings", {})
        ranking = rankings.get(group_id, []) if isinstance(rankings, dict) else []
        return [str(team_id) for team_id in ranking]

    def _round_entries(self, round_name: str) -> list[dict[str, Any]]:
        knockout = self.session.data.get("knockout", {})
        entries = knockout.get(round_name, []) if isinstance(knockout, dict) else []
        return [entry for entry in entries if isinstance(entry, dict)]

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

    def _jump_to_edit_section(self, section: str) -> None:
        self.pending_values = []
        self.review_group_id = None
        self.review_round_name = None

        if section == "group_stage":
            while True:
                try:
                    self.session.data = undo_last_prediction_step(
                        self.session.model,
                        self.session.data,
                    )
                except PredictionValidationError:
                    break
            first_group = (
                self.session.model.groups[0].label
                if self.session.model.groups
                else "the group stage"
            )
            self.notice = f"Group stage picks reset. Continue from {first_group}."
            return

        labels = {
            "third_place_qualifiers": "Third-place qualifiers",
            **{round_name: ROUND_LABELS[round_name] for round_name in ROUND_ORDER},
        }
        if section not in labels:
            raise PredictionValidationError("Choose a valid section to edit.")

        while True:
            step = next_prediction_step(self.session.model, self.session.data)
            if (
                section == "third_place_qualifiers"
                and step.kind == "third_place"
            ) or (
                section in ROUND_ORDER
                and step.kind == "knockout"
                and step.round_name == section
                and not self._round_entries(section)
            ):
                self.notice = f"{labels[section]} reset. Continue from here."
                return

            try:
                self.session.data = undo_last_prediction_step(
                    self.session.model,
                    self.session.data,
                )
            except PredictionValidationError as exc:
                raise PredictionValidationError("That section is not ready to edit yet.") from exc


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


def _review_title(group: Group | None, round_name: str | None) -> str | None:
    if group is not None:
        return f"{group.label} Complete"
    if round_name is not None:
        return f"{ROUND_LABELS[round_name]} Complete"
    return None


def _add_knockout_recap_fields(
    embed: discord.Embed,
    model: TournamentModel,
    data: dict[str, Any],
    round_name: str,
) -> None:
    label = ROUND_LABELS[round_name]
    lines = [
        _format_knockout_match(model, index, match)
        for index, match in enumerate(get_round_matches(model, data, round_name), start=1)
    ]
    if not lines:
        embed.add_field(name=label, value="No picks recorded yet.", inline=False)
        return

    for index, chunk in enumerate(_chunk_field_lines(lines), start=1):
        name = label if index == 1 else f"{label} continued"
        embed.add_field(name=name, value=chunk, inline=False)


def _add_submission_review_fields(
    embed: discord.Embed,
    model: TournamentModel,
    data: dict[str, Any],
) -> None:
    embed.add_field(
        name="Prediction summary",
        value=_format_summary(model, data),
        inline=False,
    )
    _add_group_stage_recap_fields(embed, model, data)
    _add_third_place_recap_field(embed, model, data)
    for round_name in ROUND_ORDER:
        _add_knockout_recap_fields(embed, model, data, round_name)


def _add_group_stage_recap_fields(
    embed: discord.Embed,
    model: TournamentModel,
    data: dict[str, Any],
) -> None:
    lines = [
        f"{group.label}: {_format_group_ranking_inline(model, data, group.id)}"
        for group in model.groups
    ]
    for index, chunk in enumerate(_chunk_field_lines(lines), start=1):
        name = "Group stage picks" if index == 1 else "Group stage picks continued"
        embed.add_field(name=name, value=chunk, inline=False)


def _add_third_place_recap_field(
    embed: discord.Embed,
    model: TournamentModel,
    data: dict[str, Any],
) -> None:
    selected = data.get("third_place_qualifier_team_ids", [])
    selected_ids = {str(team_id) for team_id in selected} if isinstance(selected, list) else set()
    if not selected_ids:
        embed.add_field(
            name="Advancing third-place teams",
            value="No picks recorded yet.",
            inline=False,
        )
        return

    try:
        thirds_by_group = predicted_third_place_by_group(model, data)
    except PredictionValidationError:
        thirds_by_group = {}

    lines = []
    for group in model.groups:
        team_id = thirds_by_group.get(group.id)
        if team_id in selected_ids:
            lines.append(f"{group.label}: {model.team(team_id).short_name}")
    if not lines:
        lines = [model.team(team_id).short_name for team_id in sorted(selected_ids)]
    embed.add_field(
        name="Advancing third-place teams",
        value="\n".join(lines)[:1024],
        inline=False,
    )


def _format_group_ranking_inline(
    model: TournamentModel,
    data: dict[str, Any],
    group_id: str,
) -> str:
    rankings = data.get("group_rankings", {})
    ranking = rankings.get(group_id, []) if isinstance(rankings, dict) else []
    if not ranking:
        return "No teams ranked yet."
    return ", ".join(
        f"{index}. {model.team(str(team_id)).short_name}"
        for index, team_id in enumerate(ranking, start=1)
    )


def _format_knockout_match(model: TournamentModel, index: int, match: RoundMatch) -> str:
    home_team_id = match.home_team_id
    away_team_id = match.away_team_id
    winner_team_id = match.winner_team_id
    home = model.team(home_team_id).short_name
    away = model.team(away_team_id).short_name
    if winner_team_id is None:
        return f"{index}. {home} vs {away}"
    winner = model.team(str(winner_team_id)).short_name
    loser = away if str(winner_team_id) == home_team_id else home
    return f"{index}. {winner} def. {loser}"


def _chunk_field_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        line = line[:1024]
        separator = 1 if current else 0
        if current and current_length + separator + len(line) > 1024:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += separator + len(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


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


def _edit_section_options() -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label="Group Stage Picks",
            value="group_stage",
            description="Restart group rankings and reseed the bracket.",
        ),
        discord.SelectOption(
            label="Third-place Qualifiers",
            value="third_place_qualifiers",
            description="Choose the advancing third-place teams again.",
        ),
        discord.SelectOption(
            label="Round of 32",
            value="round_of_32",
            description="Start at the first Round of 32 pick.",
        ),
        discord.SelectOption(
            label="Round of 16",
            value="round_of_16",
            description="Start at the first Round of 16 pick.",
        ),
        discord.SelectOption(
            label="Quarter-finals",
            value="quarter_finals",
            description="Start at the first quarter-final pick.",
        ),
        discord.SelectOption(
            label="Semi-finals",
            value="semi_finals",
            description="Start at the first semi-final pick.",
        ),
        discord.SelectOption(
            label="Third-place match",
            value="third_place",
            description="Start at the third-place match pick.",
        ),
        discord.SelectOption(
            label="Final",
            value="final",
            description="Start at the final pick.",
        ),
    ]


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
