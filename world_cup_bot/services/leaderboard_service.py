from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from world_cup_bot.data.repositories import (
    GuildSettingsRepository,
    PredictionRepository,
    PredictionScore,
    PredictionScoreRepository,
    ResultRepository,
    StoredMatchResult,
    TournamentConfigRepository,
)
from world_cup_bot.domain.predictions import TournamentModel
from world_cup_bot.domain.scoring import SCORING_VERSION, ScoringRules, score_prediction
from world_cup_bot.domain.standings import MatchResult


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


class LeaderboardService:
    def __init__(self, pool: Any) -> None:
        self.settings = GuildSettingsRepository(pool)
        self.tournaments = TournamentConfigRepository(pool)
        self.predictions = PredictionRepository(pool)
        self.results = ResultRepository(pool)
        self.scores = PredictionScoreRepository(pool)

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
        )
        for ranked in scores:
            if ranked.score.user_id == user_id:
                return ranked
        return None

    async def top_scores(
        self,
        *,
        guild_id: str,
        limit: int = 10,
    ) -> list[RankedScore]:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise LeaderboardServiceError("Ask an admin to import tournament data first.")
        scores = await self._ranked_scores(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        return scores[:limit]

    async def _ranked_scores(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[RankedScore]:
        scores = await self.scores.list_scores(
            guild_id=guild_id,
            tournament_config_id=tournament_config_id,
        )
        ranked: list[RankedScore] = []
        previous_points: int | None = None
        current_rank = 0
        for index, score in enumerate(scores, start=1):
            if previous_points != score.total_points:
                current_rank = index
                previous_points = score.total_points
            ranked.append(RankedScore(rank=current_rank, score=score))
        return ranked


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
