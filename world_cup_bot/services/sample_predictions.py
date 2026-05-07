from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from world_cup_bot.data.repositories import (
    PredictionEntry,
    PredictionRepository,
    TournamentConfigRepository,
)
from world_cup_bot.domain.predictions import (
    ROUND_ORDER,
    TournamentModel,
    empty_prediction_data,
    get_round_matches,
    is_submission_complete,
    predicted_third_place_by_group,
    record_group_pick,
    record_knockout_winner,
    record_third_place_qualifiers,
)
from world_cup_bot.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardServiceError,
)


FAKE_PREDICTION_USERS = (
    ("990000000000000001", "Sample Predictor 1"),
    ("990000000000000002", "Sample Predictor 2"),
    ("990000000000000003", "Sample Predictor 3"),
)


class SamplePredictionSeedError(RuntimeError):
    """Raised when fake prediction seeding cannot run."""


@dataclass(frozen=True)
class SeededFakePrediction:
    user_id: str
    display_name: str
    revision: int


@dataclass(frozen=True)
class SamplePredictionSeedSummary:
    guild_id: str
    tournament_config_id: int
    seeded_predictions: tuple[SeededFakePrediction, ...]
    recalculated_score_count: int | None
    recalculation_error: str | None = None


class _Randomizer(Protocol):
    def shuffle(self, x: list[str]) -> None:
        ...

    def sample(self, population: Sequence[Any], k: int) -> list[Any]:
        ...

    def choice(self, seq: Sequence[Any]) -> Any:
        ...


class SamplePredictionSeedService:
    def __init__(
        self,
        pool: Any,
        *,
        randomizer: _Randomizer | None = None,
    ) -> None:
        self.tournaments = TournamentConfigRepository(pool)
        self.predictions = PredictionRepository(pool)
        self.leaderboard = LeaderboardService(pool)
        self.randomizer = randomizer or random.SystemRandom()

    async def seed_fake_predictions(
        self,
        *,
        guild_id: str,
    ) -> SamplePredictionSeedSummary:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise SamplePredictionSeedError(
                "Run `/admin setup` in the target guild before seeding predictions."
            )

        model = TournamentModel.from_config(tournament.config)
        seeded: list[SeededFakePrediction] = []
        for user_id, display_name in FAKE_PREDICTION_USERS:
            data = build_random_prediction_data(model, randomizer=self.randomizer)
            entry = await self.predictions.submit_prediction(
                guild_id=guild_id,
                tournament_config_id=tournament.id,
                user_id=user_id,
                display_name=display_name,
                data=data,
            )
            seeded.append(_seeded_entry(entry))

        recalculated_score_count: int | None = None
        recalculation_error: str | None = None
        try:
            recalculation = await self.leaderboard.recalculate(guild_id=guild_id)
            recalculated_score_count = recalculation.scored_prediction_count
        except LeaderboardServiceError as exc:
            recalculation_error = str(exc)

        return SamplePredictionSeedSummary(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
            seeded_predictions=tuple(seeded),
            recalculated_score_count=recalculated_score_count,
            recalculation_error=recalculation_error,
        )


def build_random_prediction_data(
    model: TournamentModel,
    *,
    randomizer: _Randomizer | None = None,
) -> dict[str, Any]:
    chooser = randomizer or random.SystemRandom()
    data = empty_prediction_data()
    for group in model.groups:
        team_ids = list(group.team_ids)
        chooser.shuffle(team_ids)
        for team_id in team_ids:
            data = record_group_pick(
                model,
                data,
                group_id=group.id,
                team_id=team_id,
            )

    predicted_thirds = predicted_third_place_by_group(model, data)
    third_place_team_ids = [
        predicted_thirds[group_id]
        for group_id in _random_qualifying_third_place_groups(model, chooser)
    ]
    chooser.shuffle(third_place_team_ids)
    data = record_third_place_qualifiers(
        model,
        data,
        team_ids=third_place_team_ids,
    )

    for round_name in ROUND_ORDER:
        for match in get_round_matches(model, data, round_name):
            data = record_knockout_winner(
                model,
                data,
                round_name=round_name,
                match_id=match.id,
                winner_team_id=chooser.choice((match.home_team_id, match.away_team_id)),
            )

    if not is_submission_complete(model, data):
        raise SamplePredictionSeedError("Generated prediction data was incomplete.")
    return data


def _random_qualifying_third_place_groups(
    model: TournamentModel,
    randomizer: _Randomizer,
) -> list[str]:
    group_ids = {group.id for group in model.groups}
    valid_rules = [
        [str(group_id) for group_id in rule.get("qualifying_groups", [])]
        for rule in model.third_place_rules
        if isinstance(rule.get("qualifying_groups"), list)
    ]
    valid_rules = [
        groups
        for groups in valid_rules
        if (
            len(groups) == model.format.third_place_qualifiers
            and len(set(groups)) == len(groups)
            and set(groups).issubset(group_ids)
        )
    ]
    if valid_rules:
        return list(randomizer.choice(valid_rules))
    return [
        str(group_id)
        for group_id in randomizer.sample(
            [group.id for group in model.groups],
            model.format.third_place_qualifiers,
        )
    ]


def _seeded_entry(entry: PredictionEntry) -> SeededFakePrediction:
    return SeededFakePrediction(
        user_id=entry.user_id,
        display_name=entry.display_name,
        revision=entry.revision,
    )
