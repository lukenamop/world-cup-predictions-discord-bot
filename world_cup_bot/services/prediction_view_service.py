from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from world_cup_bot.data.repositories import (
    GuildSettings,
    GuildSettingsRepository,
    PredictionEntry,
    PredictionRepository,
    PredictionScore,
    PredictionScoreRepository,
    ResultRepository,
    ResultSyncRun,
    StoredMatchResult,
    TournamentConfigRepository,
    UserPreferences,
    UserPreferencesRepository,
)
from world_cup_bot.domain.locks import effective_lock_deadline, is_prediction_locked
from world_cup_bot.domain.predictions import (
    ROUND_LABELS,
    ROUND_ORDER,
    PredictionValidationError,
    TournamentModel,
    get_round_matches,
    prediction_summary,
)
from world_cup_bot.domain.scoring import actual_tournament_data
from world_cup_bot.domain.standings import MatchResult


class PredictionViewServiceError(RuntimeError):
    """Raised when a submitted prediction cannot be displayed."""


@dataclass(frozen=True)
class PredictionSnapshot:
    guild_id: str
    viewer_user_id: str
    target_user_id: str
    display_name: str
    tournament_name: str
    model: TournamentModel
    settings: GuildSettings | None
    preferences: UserPreferences
    entry: PredictionEntry
    data: dict[str, Any]
    score: PredictionScore | None
    latest_sync_run: ResultSyncRun | None
    lock_deadline_utc: datetime | None
    is_locked: bool

    @property
    def is_own_prediction(self) -> bool:
        return self.viewer_user_id == self.target_user_id

    @property
    def can_view_full_prediction(self) -> bool:
        return self.is_own_prediction or self.preferences.share_full_bracket


@dataclass(frozen=True)
class RenderStatus:
    label: str
    state: str


@dataclass(frozen=True)
class GroupRenderRow:
    position: int
    team_name: str
    status: RenderStatus
    third_place_status: RenderStatus | None = None


@dataclass(frozen=True)
class GroupRenderSection:
    label: str
    rows: tuple[GroupRenderRow, ...]


@dataclass(frozen=True)
class GroupSheetRenderModel:
    title: str
    subtitle: str
    meta: tuple[str, ...]
    groups: tuple[GroupRenderSection, ...]


@dataclass(frozen=True)
class BracketRenderMatch:
    round_label: str
    match_id: str
    home_team_name: str
    away_team_name: str
    winner_team_name: str
    status: RenderStatus


@dataclass(frozen=True)
class BracketRenderModel:
    title: str
    subtitle: str
    meta: tuple[str, ...]
    matches: tuple[BracketRenderMatch, ...]


class PredictionViewService:
    def __init__(self, pool: Any) -> None:
        self.settings = GuildSettingsRepository(pool)
        self.tournaments = TournamentConfigRepository(pool)
        self.predictions = PredictionRepository(pool)
        self.preferences = UserPreferencesRepository(pool)
        self.results = ResultRepository(pool)
        self.scores = PredictionScoreRepository(pool)

    async def snapshot(
        self,
        *,
        guild_id: str,
        target_user_id: str,
        viewer_user_id: str,
    ) -> PredictionSnapshot:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise PredictionViewServiceError("Ask an admin to import tournament data first.")

        entry = await self.predictions.get_entry(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
            user_id=target_user_id,
        )
        if entry is None or entry.submitted_data is None:
            raise PredictionViewServiceError("That user has not submitted a prediction yet.")

        settings = await self.settings.get(guild_id)
        model = TournamentModel.from_config(tournament.config)
        latest_sync_run = await self.results.latest_sync_run(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        lock_deadline = effective_lock_deadline(
            configured_deadline_utc=settings.lock_deadline_utc if settings else None,
            tournament_config=tournament.config,
        )
        score = await self.scores.get_user_score(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
            user_id=target_user_id,
        )
        preferences = await self.preferences.get(
            guild_id=guild_id,
            user_id=target_user_id,
        )
        preferences = _preferences_with_guild_default(
            preferences=preferences,
            settings=settings,
        )
        return PredictionSnapshot(
            guild_id=guild_id,
            viewer_user_id=viewer_user_id,
            target_user_id=target_user_id,
            display_name=entry.display_name,
            tournament_name=tournament.tournament_name,
            model=model,
            settings=settings,
            preferences=preferences,
            entry=entry,
            data=dict(entry.submitted_data),
            score=score,
            latest_sync_run=latest_sync_run,
            lock_deadline_utc=lock_deadline,
            is_locked=is_prediction_locked(
                configured_deadline_utc=settings.lock_deadline_utc if settings else None,
                tournament_config=tournament.config,
            ),
        )

    async def actual_data(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        model: TournamentModel,
    ) -> dict[str, Any]:
        stored_results = await self.results.list_match_results(
            guild_id=guild_id,
            tournament_config_id=tournament_config_id,
        )
        return actual_tournament_data(
            model,
            [_to_domain_result(result) for result in stored_results],
        )

    async def set_share_full_bracket(
        self,
        *,
        guild_id: str,
        user_id: str,
        share_full_bracket: bool,
    ) -> UserPreferences:
        return await self.preferences.set_share_full_bracket(
            guild_id=guild_id,
            user_id=user_id,
            share_full_bracket=share_full_bracket,
        )


def _preferences_with_guild_default(
    *,
    preferences: UserPreferences,
    settings: GuildSettings | None,
) -> UserPreferences:
    if preferences.updated_at is not None or settings is None:
        return preferences
    return UserPreferences(
        guild_id=preferences.guild_id,
        user_id=preferences.user_id,
        share_full_bracket=bool(
            settings.privacy_defaults.get("share_full_bracket", False)
        ),
        updated_at=None,
    )


def public_prediction_lines(snapshot: PredictionSnapshot) -> tuple[str, ...]:
    try:
        summary = prediction_summary(snapshot.model, snapshot.data)
    except PredictionValidationError:
        return ("Submitted prediction could not be summarized.",)

    model = snapshot.model
    lines = [
        f"Champion: {model.team(summary.champion_team_id).short_name}",
        f"Runner-up: {model.team(summary.runner_up_team_id).short_name}",
        f"Third place: {model.team(summary.third_place_team_id).short_name}",
    ]
    if snapshot.score is not None:
        lines.append(f"Points: {snapshot.score.total_points}")
    return tuple(lines)


def group_sheet_render_model(
    snapshot: PredictionSnapshot,
    actual_data: Mapping[str, Any],
) -> GroupSheetRenderModel:
    predicted_rankings = _rankings(snapshot.data)
    actual_rankings = _rankings(actual_data)
    predicted_thirds = set(_strings(snapshot.data.get("third_place_qualifier_team_ids")))
    actual_thirds = set(_strings(actual_data.get("third_place_qualifier_team_ids")))
    actual_thirds_ready = bool(actual_thirds)

    sections: list[GroupRenderSection] = []
    for group in snapshot.model.groups:
        predicted = predicted_rankings.get(group.id, [])
        actual = actual_rankings.get(group.id, [])
        rows: list[GroupRenderRow] = []
        for index, team_id in enumerate(predicted, start=1):
            actual_team_id = actual[index - 1] if len(actual) >= index else None
            status = _status(
                expected=team_id,
                actual=actual_team_id,
                pending=actual_team_id is None,
            )
            third_status = None
            if index == 3 and team_id in predicted_thirds:
                third_status = _status(
                    expected=team_id,
                    actual=team_id if team_id in actual_thirds else None,
                    pending=not actual_thirds_ready,
                    correct_label="3P OK",
                    incorrect_label="3P X",
                )
            rows.append(
                GroupRenderRow(
                    position=index,
                    team_name=snapshot.model.team(team_id).short_name,
                    status=status,
                    third_place_status=third_status,
                )
            )
        sections.append(GroupRenderSection(label=group.label, rows=tuple(rows)))

    return GroupSheetRenderModel(
        title=f"{snapshot.display_name} groups",
        subtitle=snapshot.tournament_name,
        meta=_snapshot_meta(snapshot),
        groups=tuple(sections),
    )


def bracket_render_model(
    snapshot: PredictionSnapshot,
    actual_data: Mapping[str, Any],
) -> BracketRenderModel:
    matches: list[BracketRenderMatch] = []
    for round_name in ROUND_ORDER:
        predicted_matches = get_round_matches(snapshot.model, snapshot.data, round_name)
        for match in predicted_matches:
            if match.winner_team_id is None:
                continue
            status = _knockout_pick_status(
                snapshot.model,
                actual_data,
                round_name,
                match.winner_team_id,
            )
            matches.append(
                BracketRenderMatch(
                    round_label=ROUND_LABELS[round_name],
                    match_id=match.id,
                    home_team_name=snapshot.model.team(match.home_team_id).short_name,
                    away_team_name=snapshot.model.team(match.away_team_id).short_name,
                    winner_team_name=snapshot.model.team(match.winner_team_id).short_name,
                    status=status,
                )
            )

    return BracketRenderModel(
        title=f"{snapshot.display_name} bracket",
        subtitle=snapshot.tournament_name,
        meta=_snapshot_meta(snapshot),
        matches=tuple(matches),
    )


def _knockout_pick_status(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    round_name: str,
    team_id: str,
) -> RenderStatus:
    if _is_advancement_round(round_name):
        actual_matches = _actual_matches(model, actual_data, round_name)
        if not actual_matches:
            return RenderStatus(label="...", state="pending")
        for match in actual_matches:
            if team_id not in {match.home_team_id, match.away_team_id}:
                continue
            if match.winner_team_id is None:
                return RenderStatus(label="...", state="pending")
            return _status(
                expected=team_id,
                actual=match.winner_team_id,
                pending=False,
            )
        return _status(
            expected=team_id,
            actual=None,
            pending=False,
        )

    actual_winners = [
        match.winner_team_id
        for match in _actual_matches(model, actual_data, round_name)
        if match.winner_team_id
    ]
    actual_winner = actual_winners[0] if actual_winners else None
    return _status(
        expected=team_id,
        actual=actual_winner,
        pending=actual_winner is None,
    )


def _is_advancement_round(round_name: str) -> bool:
    return round_name in {
        "round_of_32",
        "round_of_16",
        "quarter_finals",
        "semi_finals",
    }


def _actual_matches(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    round_name: str,
) -> tuple[Any, ...]:
    try:
        return get_round_matches(model, actual_data, round_name)
    except PredictionValidationError:
        return ()


def _snapshot_meta(snapshot: PredictionSnapshot) -> tuple[str, ...]:
    submitted = (
        f"Submitted {snapshot.entry.submitted_updated_at:%Y-%m-%d %H:%M UTC}"
        if snapshot.entry.submitted_updated_at
        else "Submitted"
    )
    lock = (
        f"Lock {snapshot.lock_deadline_utc:%Y-%m-%d %H:%M UTC}"
        if snapshot.lock_deadline_utc
        else "Lock not configured"
    )
    sync = (
        f"Last sync {snapshot.latest_sync_run.finished_at:%Y-%m-%d %H:%M UTC}"
        if snapshot.latest_sync_run and snapshot.latest_sync_run.finished_at
        else "No completed sync"
    )
    lock_state = "Locked" if snapshot.is_locked else "Open"
    return (submitted, lock, lock_state, sync)


def _status(
    *,
    expected: str,
    actual: str | None,
    pending: bool,
    correct_label: str = "OK",
    incorrect_label: str = "X",
) -> RenderStatus:
    if pending:
        return RenderStatus(label="...", state="pending")
    if expected == actual:
        return RenderStatus(label=correct_label, state="correct")
    return RenderStatus(label=incorrect_label, state="incorrect")


def _rankings(data: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = data.get("group_rankings")
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(group_id): _strings(team_ids)
        for group_id, team_ids in raw.items()
    }


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _to_domain_result(result: StoredMatchResult) -> MatchResult:
    return MatchResult(
        match_id=result.match_id,
        stage=result.stage,
        home_team_id=result.home_team_id,
        away_team_id=result.away_team_id,
        status=result.status,
        home_score=result.home_score,
        away_score=result.away_score,
        group_id=result.group_id,
        round_name=result.round_name,
        winner_team_id=result.winner_team_id,
        played_at=result.played_at,
    )
