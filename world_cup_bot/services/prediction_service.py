from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from world_cup_bot.data.repositories import (
    ActiveTournamentConfig,
    GuildSettings,
    GuildSettingsRepository,
    PredictionEntry,
    PredictionRepository,
    TournamentConfigRepository,
)
from world_cup_bot.domain.locks import effective_lock_deadline, is_prediction_locked
from world_cup_bot.domain.predictions import (
    PredictionValidationError,
    TournamentModel,
    empty_prediction_data,
    is_submission_complete,
)


class PredictionServiceError(RuntimeError):
    """Raised when prediction entry cannot proceed."""


@dataclass(frozen=True)
class PredictionSessionState:
    guild_id: str
    tournament: ActiveTournamentConfig
    model: TournamentModel
    settings: GuildSettings
    entry: PredictionEntry | None
    data: dict[str, Any]
    lock_deadline_utc: datetime | None
    edit_existing: bool


class PredictionService:
    def __init__(self, pool: Any) -> None:
        self.settings = GuildSettingsRepository(pool)
        self.tournaments = TournamentConfigRepository(pool)
        self.predictions = PredictionRepository(pool)

    async def start_prediction(
        self,
        *,
        guild_id: str,
        user_id: str,
        edit_existing: bool,
    ) -> PredictionSessionState:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise PredictionServiceError("Ask an admin to import tournament data first.")

        settings = await self.settings.get(guild_id)
        if settings is None or not settings.predictions_open:
            raise PredictionServiceError("Predictions are closed for this server.")

        model = TournamentModel.from_config(tournament.config)
        lock_deadline = effective_lock_deadline(
            configured_deadline_utc=settings.lock_deadline_utc,
            tournament_config=tournament.config,
        )
        if is_prediction_locked(
            configured_deadline_utc=settings.lock_deadline_utc,
            tournament_config=tournament.config,
        ):
            raise PredictionServiceError("Predictions are locked for this tournament.")

        entry = await self.predictions.get_entry(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
            user_id=user_id,
        )
        data = self._session_data(entry, edit_existing=edit_existing)
        return PredictionSessionState(
            guild_id=guild_id,
            tournament=tournament,
            model=model,
            settings=settings,
            entry=entry,
            data=data,
            lock_deadline_utc=lock_deadline,
            edit_existing=edit_existing,
        )

    async def submit(
        self,
        *,
        state: PredictionSessionState,
        user_id: str,
        display_name: str,
        data: dict[str, Any],
    ) -> PredictionEntry:
        await self._ensure_session_can_write(state)
        await self._ensure_submit_matches_session(state, user_id=user_id)
        if not is_submission_complete(state.model, data):
            raise PredictionValidationError("Prediction is not complete yet.")
        return await self.predictions.submit_prediction(
            guild_id=state.guild_id,
            tournament_config_id=state.tournament.id,
            user_id=user_id,
            display_name=display_name,
            data=data,
        )

    async def _ensure_session_can_write(self, state: PredictionSessionState) -> None:
        settings = await self.settings.get(state.guild_id)
        if settings is None or not settings.predictions_open:
            raise PredictionServiceError("Predictions are closed for this server.")

        tournament = await self.tournaments.get_active_config(state.guild_id)
        if tournament is None:
            raise PredictionServiceError("Ask an admin to import tournament data first.")
        if tournament.id != state.tournament.id:
            raise PredictionServiceError(
                "Tournament data changed. Start a new prediction session."
            )

        if is_prediction_locked(
            configured_deadline_utc=settings.lock_deadline_utc,
            tournament_config=tournament.config,
        ):
            raise PredictionServiceError("Predictions are locked for this tournament.")

    async def _ensure_submit_matches_session(
        self,
        state: PredictionSessionState,
        *,
        user_id: str,
    ) -> None:
        entry = await self.predictions.get_entry(
            guild_id=state.guild_id,
            tournament_config_id=state.tournament.id,
            user_id=user_id,
        )
        has_submission = entry is not None and entry.submitted_data is not None
        if state.edit_existing:
            if not has_submission:
                raise PredictionServiceError(
                    "You do not have a submitted prediction to edit yet. Use `/predict` first."
                )
            return
        if has_submission:
            raise PredictionServiceError(
                "You already have a submitted prediction. Use `/edit` before lock to replace it."
            )

    def _session_data(
        self,
        entry: PredictionEntry | None,
        *,
        edit_existing: bool,
    ) -> dict[str, Any]:
        if entry is None:
            if edit_existing:
                raise PredictionServiceError(
                    "You do not have a submitted prediction to edit yet. Use `/predict` first."
                )
            return empty_prediction_data()
        if entry.submitted_data is None:
            if edit_existing:
                raise PredictionServiceError(
                    "You do not have a submitted prediction to edit yet. Use `/predict` first."
                )
            return empty_prediction_data()
        if edit_existing:
            return dict(entry.submitted_data)
        raise PredictionServiceError(
            "You already have a submitted prediction. Use `/edit` before lock to replace it."
        )
