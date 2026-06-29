from __future__ import annotations

import json
import random
import unittest
from datetime import datetime, timezone
from pathlib import Path

from world_cup_bot.data.repositories import (
    ActiveTournamentConfig,
    PredictionEntry,
    RankedPredictionScoreRow,
    PredictionScore,
)
from world_cup_bot.domain.predictions import TournamentModel
from world_cup_bot.domain.scoring import SCORING_VERSION
from world_cup_bot.services.leaderboard_service import LeaderboardService
from world_cup_bot.services.sample_predictions import build_random_prediction_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LeaderboardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_top_scores_includes_submitted_entries_without_score_rows(self) -> None:
        config = _canonical_tournament_config()
        model = TournamentModel.from_config(config)
        entries = [
            _prediction_entry(
                entry_id=1,
                user_id="user-1",
                display_name="User One",
                data=build_random_prediction_data(model, randomizer=random.Random(1)),
            ),
            _prediction_entry(
                entry_id=2,
                user_id="user-2",
                display_name="User Two",
                data=build_random_prediction_data(model, randomizer=random.Random(2)),
            ),
        ]
        service = _leaderboard_service(config=config, entries=entries, scores=[])

        ranked_scores = await service.top_scores(guild_id="guild-1", limit=10)

        self.assertEqual(
            [ranked.score.user_id for ranked in ranked_scores],
            ["user-1", "user-2"],
        )
        self.assertEqual([ranked.rank for ranked in ranked_scores], [1, 1])
        self.assertEqual([ranked.score.total_points for ranked in ranked_scores], [0, 0])
        self.assertTrue(all(ranked.champion_team_name for ranked in ranked_scores))

    async def test_top_scores_limit_none_returns_all_ranked_scores(self) -> None:
        config = _canonical_tournament_config()
        model = TournamentModel.from_config(config)
        entries = [
            _prediction_entry(
                entry_id=index,
                user_id=f"user-{index}",
                display_name=f"User {index}",
                data=build_random_prediction_data(model, randomizer=random.Random(index)),
            )
            for index in range(1, 4)
        ]
        service = _leaderboard_service(config=config, entries=entries, scores=[])

        ranked_scores = await service.top_scores(guild_id="guild-1", limit=None)

        self.assertEqual(
            [ranked.score.user_id for ranked in ranked_scores],
            ["user-1", "user-2", "user-3"],
        )

    async def test_top_scores_ranks_unscored_submissions_below_scored_entries(self) -> None:
        config = _canonical_tournament_config()
        model = TournamentModel.from_config(config)
        scored_entry = _prediction_entry(
            entry_id=1,
            user_id="user-1",
            display_name="User One",
            data=build_random_prediction_data(model, randomizer=random.Random(1)),
        )
        unscored_entry = _prediction_entry(
            entry_id=2,
            user_id="user-2",
            display_name="User Two",
            data=build_random_prediction_data(model, randomizer=random.Random(2)),
        )
        service = _leaderboard_service(
            config=config,
            entries=[scored_entry, unscored_entry],
            scores=[
                PredictionScore(
                    prediction_entry_id=1,
                    guild_id="guild-1",
                    tournament_config_id=1,
                    user_id="user-1",
                    display_name="User One",
                    total_points=7,
                    group_points=7,
                    knockout_points=0,
                    breakdown={"groups": {}, "knockout": {}},
                    scoring_version=SCORING_VERSION,
                    recalculated_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
                )
            ],
        )

        ranked_scores = await service.top_scores(guild_id="guild-1", limit=10)

        self.assertEqual(
            [
                (ranked.rank, ranked.score.user_id, ranked.score.total_points)
                for ranked in ranked_scores
            ],
            [(1, "user-1", 7), (2, "user-2", 0)],
        )


def _leaderboard_service(
    *,
    config: dict[str, object],
    entries: list[PredictionEntry],
    scores: list[PredictionScore],
) -> LeaderboardService:
    service = LeaderboardService(pool=None)
    service.tournaments = _TournamentRepository(config)
    service.predictions = _PredictionRepository(entries)
    service.scores = _ScoreRepository(entries, scores)
    return service


def _canonical_tournament_config() -> dict[str, object]:
    return json.loads(
        (PROJECT_ROOT / "config" / "tournaments" / "2026_world_cup.json").read_text()
    )


def _prediction_entry(
    *,
    entry_id: int,
    user_id: str,
    display_name: str,
    data: dict[str, object],
) -> PredictionEntry:
    submitted_at = datetime(2026, 5, entry_id, 12, tzinfo=timezone.utc)
    return PredictionEntry(
        id=entry_id,
        guild_id="guild-1",
        tournament_config_id=1,
        user_id=user_id,
        display_name=display_name,
        draft_data=data,
        submitted_data=data,
        revision=1,
        draft_updated_at=submitted_at,
        submitted_at=submitted_at,
        submitted_updated_at=submitted_at,
    )


class _TournamentRepository:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config

    async def get_active_config(self, guild_id: str) -> ActiveTournamentConfig:
        return ActiveTournamentConfig(
            id=1,
            tournament_id="2026-world-cup",
            tournament_name="2026 World Cup",
            schema_version="test",
            config_hash="hash-1",
            imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            imported_by_user_id="admin-1",
            config=self.config,
        )


class _PredictionRepository:
    def __init__(self, entries: list[PredictionEntry]) -> None:
        self.entries = entries

    async def list_submitted_entries(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[PredictionEntry]:
        return self.entries


class _ScoreRepository:
    def __init__(
        self,
        entries: list[PredictionEntry],
        scores: list[PredictionScore],
    ) -> None:
        self.entries = entries
        self.scores = scores

    async def list_ranked_score_rows(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        limit: int | None,
    ) -> list[RankedPredictionScoreRow]:
        rows = _ranked_rows(self.entries, self.scores)
        return rows if limit is None else rows[:limit]

    async def get_ranked_score_row(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
    ) -> RankedPredictionScoreRow | None:
        for row in _ranked_rows(self.entries, self.scores):
            if row.entry.user_id == user_id:
                return row
        return None

    async def list_scores(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[PredictionScore]:
        return list(self.scores)


def _ranked_rows(
    entries: list[PredictionEntry],
    scores: list[PredictionScore],
) -> list[RankedPredictionScoreRow]:
    score_by_entry_id = {score.prediction_entry_id: score for score in scores}
    sortable = [
        (
            score_by_entry_id.get(entry.id)
            or PredictionScore(
                prediction_entry_id=entry.id,
                guild_id=entry.guild_id,
                tournament_config_id=entry.tournament_config_id,
                user_id=entry.user_id,
                display_name=entry.display_name,
                total_points=0,
                group_points=0,
                knockout_points=0,
                breakdown={"groups": {}, "knockout": {}},
                scoring_version=SCORING_VERSION,
                recalculated_at=(
                    entry.submitted_updated_at
                    or entry.submitted_at
                    or entry.draft_updated_at
                    or datetime.now(timezone.utc)
                ),
            ),
            entry,
        )
        for entry in entries
    ]
    sortable.sort(
        key=lambda item: (
            -item[0].total_points,
            item[0].recalculated_at,
            item[0].display_name.casefold(),
            item[0].user_id,
        )
    )
    rows: list[RankedPredictionScoreRow] = []
    previous_points: int | None = None
    current_rank = 0
    for index, (score, entry) in enumerate(sortable, start=1):
        if score.total_points != previous_points:
            current_rank = index
            previous_points = score.total_points
        rows.append(
            RankedPredictionScoreRow(
                rank=current_rank,
                entry=entry,
                score=score_by_entry_id.get(entry.id),
            )
        )
    return rows
