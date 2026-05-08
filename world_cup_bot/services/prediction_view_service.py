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
    TieBreakerAdjudicationRepository,
    TournamentConfigRepository,
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
from world_cup_bot.domain.scoring import ScoringRules, actual_tournament_data
from world_cup_bot.domain.standings import MatchResult, StandingResolutionError


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
        return True


@dataclass(frozen=True)
class RenderStatus:
    label: str
    state: str


@dataclass(frozen=True)
class GroupRenderRow:
    position: int
    team_name: str
    flag_code: str | None
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
    home_flag_code: str | None
    home_status: RenderStatus
    away_team_name: str
    away_flag_code: str | None
    away_status: RenderStatus
    winner_team_name: str
    winner_flag_code: str | None
    status: RenderStatus


@dataclass(frozen=True)
class BracketRenderModel:
    title: str
    subtitle: str
    meta: tuple[str, ...]
    matches: tuple[BracketRenderMatch, ...]
    champion_status: RenderStatus = RenderStatus(label="...", state="pending")
    runner_up_status: RenderStatus = RenderStatus(label="...", state="pending")
    third_place_status: RenderStatus | None = None


class PredictionViewService:
    def __init__(self, pool: Any) -> None:
        self.settings = GuildSettingsRepository(pool)
        self.tournaments = TournamentConfigRepository(pool)
        self.predictions = PredictionRepository(pool)
        self.results = ResultRepository(pool)
        self.scores = PredictionScoreRepository(pool)
        self.tie_breakers = TieBreakerAdjudicationRepository(pool)

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
        return PredictionSnapshot(
            guild_id=guild_id,
            viewer_user_id=viewer_user_id,
            target_user_id=target_user_id,
            display_name=entry.display_name,
            tournament_name=tournament.tournament_name,
            model=model,
            settings=settings,
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
        tournament = await self.tournaments.get_active_config(guild_id)
        adjudications = []
        if tournament is not None and tournament.id == tournament_config_id:
            adjudications = [
                adjudication.to_domain()
                for adjudication in await self.tie_breakers.list_for_config(
                    tournament_id=tournament.tournament_id,
                    config_hash=tournament.config_hash,
                )
            ]
        try:
            return actual_tournament_data(
                model,
                [_to_domain_result(result) for result in stored_results],
                adjudications=adjudications,
            )
        except StandingResolutionError as exc:
            raise PredictionViewServiceError(
                "Cannot render current results until official tie-breakers are "
                f"resolved. {exc}"
            ) from exc


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
    return tuple(lines)


def full_view_summary(
    snapshot: PredictionSnapshot,
    *,
    current_view: str = "summary",
) -> str:
    if current_view == "bracket":
        return (
            "Bracket image shown here. "
            f"Use {_groups_command_hint(snapshot)} for groups."
        )
    if current_view == "groups":
        return (
            "Group image shown here. "
            f"Use {_bracket_command_hint(snapshot)} for the bracket."
        )
    return f"Use {_bracket_command_hint(snapshot)} or {_groups_command_hint(snapshot)}."


def _bracket_command_hint(snapshot: PredictionSnapshot) -> str:
    return "`/bracket`" if snapshot.is_own_prediction else "`/bracket user:<member>`"


def _groups_command_hint(snapshot: PredictionSnapshot) -> str:
    return "`/groups`" if snapshot.is_own_prediction else "`/groups user:<member>`"


def group_sheet_render_model(
    snapshot: PredictionSnapshot,
    actual_data: Mapping[str, Any],
) -> GroupSheetRenderModel:
    predicted_rankings = _rankings(snapshot.data)
    actual_rankings = _rankings(actual_data)
    predicted_thirds = set(_strings(snapshot.data.get("third_place_qualifier_team_ids")))
    actual_thirds = set(_strings(actual_data.get("third_place_qualifier_team_ids")))
    actual_thirds_ready = bool(actual_thirds)
    rules = ScoringRules.from_mapping(
        snapshot.settings.scoring_rules if snapshot.settings else None
    )

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
                correct_label=f"+{_group_pick_points(index, rules)}",
            )
            third_status = None
            if index == 3 and team_id in predicted_thirds:
                third_status = _status(
                    expected=team_id,
                    actual=team_id if team_id in actual_thirds else None,
                    pending=not actual_thirds_ready,
                    correct_label=f"+{rules.group_third_place_qualifier}",
                )
            rows.append(
                GroupRenderRow(
                    position=index,
                    team_name=snapshot.model.team(team_id).short_name,
                    flag_code=snapshot.model.team(team_id).country_code,
                    status=status,
                    third_place_status=third_status,
                )
            )
        sections.append(GroupRenderSection(label=group.label, rows=tuple(rows)))

    return GroupSheetRenderModel(
        title=f"{snapshot.display_name}'s groups",
        subtitle=snapshot.tournament_name,
        meta=_snapshot_meta(snapshot),
        groups=tuple(sections),
    )


def bracket_render_model(
    snapshot: PredictionSnapshot,
    actual_data: Mapping[str, Any],
) -> BracketRenderModel:
    matches: list[BracketRenderMatch] = []
    rules = ScoringRules.from_mapping(
        snapshot.settings.scoring_rules if snapshot.settings else None
    )
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
                rules,
            )
            matches.append(
                BracketRenderMatch(
                    round_label=ROUND_LABELS[round_name],
                    match_id=match.id,
                    home_team_name=snapshot.model.team(match.home_team_id).short_name,
                    home_flag_code=snapshot.model.team(match.home_team_id).country_code,
                    home_status=_knockout_team_status(
                        snapshot.model,
                        actual_data,
                        round_name,
                        match.home_team_id,
                        rules,
                    ),
                    away_team_name=snapshot.model.team(match.away_team_id).short_name,
                    away_flag_code=snapshot.model.team(match.away_team_id).country_code,
                    away_status=_knockout_team_status(
                        snapshot.model,
                        actual_data,
                        round_name,
                        match.away_team_id,
                        rules,
                    ),
                    winner_team_name=snapshot.model.team(match.winner_team_id).short_name,
                    winner_flag_code=snapshot.model.team(match.winner_team_id).country_code,
                    status=status,
                )
            )

    return BracketRenderModel(
        title=f"{snapshot.display_name}'s bracket",
        subtitle=snapshot.tournament_name,
        meta=_snapshot_meta(snapshot),
        matches=tuple(matches),
        champion_status=_placement_status(
            snapshot.model,
            snapshot.data,
            actual_data,
            "champion",
            rules.champion,
        ),
        runner_up_status=_placement_status(
            snapshot.model,
            snapshot.data,
            actual_data,
            "runner_up",
            rules.runner_up,
        ),
        third_place_status=_placement_status(
            snapshot.model,
            snapshot.data,
            actual_data,
            "third_place",
            rules.third_place_winner,
        ),
    )


def _knockout_pick_status(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    round_name: str,
    team_id: str,
    rules: ScoringRules,
) -> RenderStatus:
    correct_label = f"+{_knockout_pick_points(round_name, rules)}"
    if _is_advancement_round(round_name):
        actual_matches = _actual_matches(model, actual_data, round_name)
        for match in actual_matches:
            if team_id not in {match.home_team_id, match.away_team_id}:
                continue
            if match.winner_team_id is None:
                return RenderStatus(label="...", state="pending")
            return _status(
                expected=team_id,
                actual=match.winner_team_id,
                pending=False,
                correct_label=correct_label,
            )
        if _team_cannot_reach_round(
            model,
            actual_data,
            team_id,
            round_name,
        ):
            return RenderStatus(label="X", state="incorrect")
        if actual_matches:
            return RenderStatus(label="X", state="incorrect")
        return RenderStatus(label="...", state="pending")

    actual_matches = _actual_matches(model, actual_data, round_name)
    for match in actual_matches:
        if team_id not in {match.home_team_id, match.away_team_id}:
            continue
        return _status(
            expected=team_id,
            actual=match.winner_team_id,
            pending=match.winner_team_id is None,
            correct_label=correct_label,
        )
    if _team_cannot_reach_round(model, actual_data, team_id, round_name):
        return RenderStatus(label="X", state="incorrect")
    if actual_matches:
        return RenderStatus(label="X", state="incorrect")
    return RenderStatus(label="...", state="pending")


def _knockout_team_status(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    round_name: str,
    team_id: str,
    rules: ScoringRules,
) -> RenderStatus:
    if round_name == "third_place":
        return _knockout_pick_status(model, actual_data, round_name, team_id, rules)
    actual_matches = _actual_matches(model, actual_data, round_name)
    for match in actual_matches:
        if team_id in {match.home_team_id, match.away_team_id}:
            return RenderStatus(
                label=f"+{_knockout_advancement_points(round_name, rules)}",
                state="correct",
            )
    if _team_cannot_reach_round(model, actual_data, team_id, round_name):
        return RenderStatus(label="X", state="incorrect")
    if not actual_matches:
        return RenderStatus(label="...", state="pending")
    return RenderStatus(label="X", state="incorrect")


def _placement_status(
    model: TournamentModel,
    prediction_data: Mapping[str, Any],
    actual_data: Mapping[str, Any],
    placement: str,
    points: int,
) -> RenderStatus:
    predicted = _placement_team_id(model, prediction_data, placement)
    actual = _placement_team_id(model, actual_data, placement)
    if (
        predicted is not None
        and actual is None
        and _placement_impossible(model, actual_data, predicted, placement)
    ):
        return RenderStatus(label="X", state="incorrect")
    return _status(
        expected=predicted or "",
        actual=actual,
        pending=actual is None,
        correct_label=f"+{points}",
    )


def _placement_team_id(
    model: TournamentModel,
    data: Mapping[str, Any],
    placement: str,
) -> str | None:
    if placement in {"champion", "runner_up"}:
        final = _actual_matches(model, data, "final")
        if len(final) != 1 or final[0].winner_team_id is None:
            return None
        if placement == "champion":
            return final[0].winner_team_id
        return final[0].loser_team_id
    third_place = _actual_matches(model, data, "third_place")
    if len(third_place) != 1:
        return None
    return third_place[0].winner_team_id


def _is_advancement_round(round_name: str) -> bool:
    return round_name in {
        "round_of_32",
        "round_of_16",
        "quarter_finals",
        "semi_finals",
    }


_MAIN_BRACKET_ROUNDS = (
    "round_of_32",
    "round_of_16",
    "quarter_finals",
    "semi_finals",
    "final",
)


def _placement_impossible(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    team_id: str,
    placement: str,
) -> bool:
    if placement in {"champion", "runner_up"}:
        return _team_cannot_reach_round(model, actual_data, team_id, "final")
    return _team_cannot_reach_round(model, actual_data, team_id, "third_place")


def _team_cannot_reach_round(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    team_id: str,
    round_name: str,
) -> bool:
    if round_name == "round_of_32":
        return False
    if _team_missed_round_of_32(model, actual_data, team_id):
        return True
    eliminated_in = _elimination_round(model, actual_data, team_id)
    if round_name == "third_place":
        if _team_won_round(model, actual_data, team_id, "semi_finals"):
            return True
        return (
            eliminated_in in _MAIN_BRACKET_ROUNDS
            and _MAIN_BRACKET_ROUNDS.index(eliminated_in)
            < _MAIN_BRACKET_ROUNDS.index("semi_finals")
        )
    if (
        round_name not in _MAIN_BRACKET_ROUNDS
        or eliminated_in not in _MAIN_BRACKET_ROUNDS
    ):
        return False
    return _MAIN_BRACKET_ROUNDS.index(eliminated_in) < _MAIN_BRACKET_ROUNDS.index(
        round_name
    )


def _team_missed_round_of_32(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    team_id: str,
) -> bool:
    round_of_32 = _actual_matches(model, actual_data, "round_of_32")
    if not round_of_32:
        return False
    return all(
        team_id not in {match.home_team_id, match.away_team_id}
        for match in round_of_32
    )


def _elimination_round(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    team_id: str,
) -> str | None:
    for round_name in _MAIN_BRACKET_ROUNDS:
        for match in _actual_matches(model, actual_data, round_name):
            if match.loser_team_id == team_id:
                return round_name
    return None


def _team_won_round(
    model: TournamentModel,
    actual_data: Mapping[str, Any],
    team_id: str,
    round_name: str,
) -> bool:
    return any(
        match.winner_team_id == team_id
        for match in _actual_matches(model, actual_data, round_name)
    )


def _knockout_advancement_points(round_name: str, rules: ScoringRules) -> int:
    values = {
        "round_of_32": rules.round_of_32_advancement,
        "round_of_16": rules.round_of_16_advancement,
        "quarter_finals": rules.quarter_final_advancement,
        "semi_finals": rules.semi_final_advancement,
        "final": rules.final_advancement,
    }
    return values.get(round_name, 0)


def _knockout_pick_points(round_name: str, rules: ScoringRules) -> int:
    values = {
        "third_place": rules.third_place_winner,
        "final": rules.champion,
    }
    return values.get(round_name, 0)


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
    return ()


def _group_pick_points(position: int, rules: ScoringRules) -> int:
    if position == 1:
        return rules.group_winner
    if position == 2:
        return rules.group_runner_up
    return 0


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
