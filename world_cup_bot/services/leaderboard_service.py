from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from world_cup_bot.data.repositories import (
    GuildSettingsRepository,
    PredictionEntry,
    PredictionRepository,
    PredictionScore,
    PredictionScoreRepository,
    ResultRepository,
    StoredMatchResult,
    TieBreakerAdjudicationRepository,
    TournamentConfigRepository,
)
from world_cup_bot.domain.predictions import (
    PredictionValidationError,
    TournamentModel,
    prediction_summary,
)
from world_cup_bot.domain.scoring import (
    SCORING_VERSION,
    ScoringRules,
    actual_tournament_data,
    score_prediction,
)
from world_cup_bot.domain.standings import MatchResult, StandingResolutionError


class LeaderboardServiceError(RuntimeError):
    """Raised when score or rank data is not available."""


@dataclass(frozen=True)
class RecalculationSummary:
    tournament_config_id: int
    scored_prediction_count: int
    result_count: int
    scoring_version: str
    recalculated_at: datetime


@dataclass(frozen=True)
class RankedScore:
    rank: int
    score: PredictionScore
    champion_team_name: str | None = None


class LeaderboardService:
    def __init__(self, pool: Any) -> None:
        self.settings = GuildSettingsRepository(pool)
        self.tournaments = TournamentConfigRepository(pool)
        self.predictions = PredictionRepository(pool)
        self.results = ResultRepository(pool)
        self.scores = PredictionScoreRepository(pool)
        self.tie_breakers = TieBreakerAdjudicationRepository(pool)

    async def recalculate(self, *, guild_id: str) -> RecalculationSummary:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise LeaderboardServiceError("Ask an admin to import tournament data first.")

        settings = await self.settings.get(guild_id)
        rules = ScoringRules.from_mapping(settings.scoring_rules if settings else None)
        model = TournamentModel.from_config(tournament.config)
        stored_results = await self.results.list_match_results(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        match_results = [_to_domain_result(result) for result in stored_results]
        adjudications = [
            adjudication.to_domain()
            for adjudication in await self.tie_breakers.list_for_config(
                tournament_id=tournament.tournament_id,
                config_hash=tournament.config_hash,
            )
        ]
        try:
            actual_tournament_data(model, match_results, adjudications=adjudications)
        except StandingResolutionError as exc:
            raise LeaderboardServiceError(
                "Cannot recalculate until official tie-breakers are resolved. "
                f"{exc}"
            ) from exc
        entries = await self.predictions.list_submitted_entries(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        recalculated_at = datetime.now(timezone.utc)
        scores: list[PredictionScore] = []
        for entry in entries:
            if entry.submitted_data is None:
                continue
            breakdown = score_prediction(
                model,
                entry.submitted_data,
                match_results,
                rules=rules,
                adjudications=adjudications,
            )
            scores.append(
                PredictionScore(
                    prediction_entry_id=entry.id,
                    guild_id=guild_id,
                    tournament_config_id=tournament.id,
                    user_id=entry.user_id,
                    display_name=entry.display_name,
                    total_points=breakdown.total_points,
                    group_points=breakdown.group_points,
                    knockout_points=breakdown.knockout_points,
                    breakdown=breakdown.details,
                    scoring_version=SCORING_VERSION,
                    recalculated_at=recalculated_at,
                )
            )

        count = await self.scores.upsert_scores(scores)
        return RecalculationSummary(
            tournament_config_id=tournament.id,
            scored_prediction_count=count,
            result_count=len(match_results),
            scoring_version=SCORING_VERSION,
            recalculated_at=recalculated_at,
        )

    async def user_score(
        self,
        *,
        guild_id: str,
        user_id: str,
    ) -> RankedScore | None:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise LeaderboardServiceError("Ask an admin to import tournament data first.")
        scores = await self._ranked_scores(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
            model=TournamentModel.from_config(tournament.config),
        )
        for ranked in scores:
            if ranked.score.user_id == user_id:
                return ranked
        return None

    async def top_scores(
        self,
        *,
        guild_id: str,
        limit: int | None = 10,
    ) -> list[RankedScore]:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise LeaderboardServiceError("Ask an admin to import tournament data first.")
        scores = await self._ranked_scores(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
            model=TournamentModel.from_config(tournament.config),
        )
        return scores if limit is None else scores[:limit]

    async def _ranked_scores(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        model: TournamentModel,
    ) -> list[RankedScore]:
        scores = await self.scores.list_scores(
            guild_id=guild_id,
            tournament_config_id=tournament_config_id,
        )
        entries = await self.predictions.list_submitted_entries(
            guild_id=guild_id,
            tournament_config_id=tournament_config_id,
        )
        score_by_entry_id = {score.prediction_entry_id: score for score in scores}
        for entry in entries:
            if entry.submitted_data is None or entry.id in score_by_entry_id:
                continue
            scores.append(
                _unscored_prediction_score(
                    entry,
                    guild_id=guild_id,
                    tournament_config_id=tournament_config_id,
                    model=model,
                )
            )
        scores.sort(
            key=lambda score: (
                -score.total_points,
                score.recalculated_at,
                score.display_name.casefold(),
                score.user_id,
            )
        )
        champion_by_entry_id = _champion_names_by_entry_id(model, entries)
        ranked: list[RankedScore] = []
        previous_points: int | None = None
        current_rank = 0
        for index, score in enumerate(scores, start=1):
            if previous_points != score.total_points:
                current_rank = index
                previous_points = score.total_points
            ranked.append(
                RankedScore(
                    rank=current_rank,
                    score=score,
                    champion_team_name=champion_by_entry_id.get(score.prediction_entry_id),
                )
            )
        return ranked


def _unscored_prediction_score(
    entry: PredictionEntry,
    *,
    guild_id: str,
    tournament_config_id: int,
    model: TournamentModel,
) -> PredictionScore:
    breakdown = (
        score_prediction(model, entry.submitted_data or {}, [])
        if entry.submitted_data is not None
        else None
    )
    score_time = (
        entry.submitted_updated_at
        or entry.submitted_at
        or entry.draft_updated_at
        or datetime.now(timezone.utc)
    )
    return PredictionScore(
        prediction_entry_id=entry.id,
        guild_id=guild_id,
        tournament_config_id=tournament_config_id,
        user_id=entry.user_id,
        display_name=entry.display_name,
        total_points=breakdown.total_points if breakdown else 0,
        group_points=breakdown.group_points if breakdown else 0,
        knockout_points=breakdown.knockout_points if breakdown else 0,
        breakdown=breakdown.details if breakdown else _empty_score_details(),
        scoring_version=SCORING_VERSION,
        recalculated_at=score_time,
    )


def _empty_score_details() -> dict[str, Any]:
    return {
        "version": SCORING_VERSION,
        "groups": {
            "points": 0,
            "group_positions": [],
            "third_place_qualifier_hits": [],
            "third_place_qualifier_points": 0,
        },
        "knockout": {
            "points": 0,
            "advancement": [],
            "placements": {
                "predicted": {},
                "actual": {},
                "third_place_points": 0,
                "champion_points": 0,
                "runner_up_points": 0,
            },
        },
    }


def _champion_names_by_entry_id(
    model: TournamentModel,
    entries: list[PredictionEntry],
) -> dict[int, str]:
    champion_names: dict[int, str] = {}
    for entry in entries:
        if entry.submitted_data is None:
            continue
        try:
            summary = prediction_summary(model, entry.submitted_data)
            champion_names[entry.id] = model.team(summary.champion_team_id).short_name
        except PredictionValidationError:
            continue
    return champion_names


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
