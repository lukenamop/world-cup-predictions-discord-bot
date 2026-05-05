from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from world_cup_bot.domain.predictions import (
    ROUND_ORDER,
    PredictionValidationError,
    TournamentModel,
    get_round_matches,
)
from world_cup_bot.domain.standings import (
    MatchResult,
    StandingAdjudication,
    actual_group_rankings,
    best_third_place_qualifiers,
    compute_group_standings,
)


SCORING_VERSION = "2026-default-v2"


@dataclass(frozen=True)
class ScoringRules:
    group_winner: int = 3
    group_runner_up: int = 2
    group_third_place_qualifier: int = 1
    round_of_32_advancement: int = 1
    round_of_16_advancement: int = 2
    quarter_final_advancement: int = 5
    semi_final_advancement: int = 10
    final_advancement: int = 15
    third_place_winner: int = 10
    champion: int = 25
    runner_up: int = 15

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> ScoringRules:
        if not value:
            return cls()
        defaults = cls()
        return cls(
            group_winner=_positive_int(value.get("group_winner"), defaults.group_winner),
            group_runner_up=_positive_int(value.get("group_runner_up"), defaults.group_runner_up),
            group_third_place_qualifier=_positive_int(
                value.get("group_third_place_qualifier"),
                defaults.group_third_place_qualifier,
            ),
            round_of_32_advancement=_positive_int(
                value.get("round_of_32_advancement"),
                defaults.round_of_32_advancement,
            ),
            round_of_16_advancement=_positive_int(
                value.get("round_of_16_advancement"),
                defaults.round_of_16_advancement,
            ),
            quarter_final_advancement=_positive_int(
                value.get("quarter_final_advancement"),
                defaults.quarter_final_advancement,
            ),
            semi_final_advancement=_positive_int(
                value.get("semi_final_advancement"),
                defaults.semi_final_advancement,
            ),
            final_advancement=_positive_int(
                value.get("final_advancement"),
                defaults.final_advancement,
            ),
            third_place_winner=_positive_int(
                value.get("third_place_winner"),
                defaults.third_place_winner,
            ),
            champion=_positive_int(value.get("champion"), defaults.champion),
            runner_up=_positive_int(value.get("runner_up"), defaults.runner_up),
        )


@dataclass(frozen=True)
class ScoreBreakdown:
    total_points: int
    group_points: int
    knockout_points: int
    details: dict[str, Any]


def score_prediction(
    model: TournamentModel,
    prediction_data: Mapping[str, Any],
    results: Iterable[MatchResult],
    *,
    rules: ScoringRules | None = None,
    adjudications: Iterable[StandingAdjudication] = (),
) -> ScoreBreakdown:
    active_rules = rules or ScoringRules()
    result_list = list(results)
    adjudication_list = tuple(adjudications)
    group_breakdown = _score_groups(
        model,
        prediction_data,
        result_list,
        active_rules,
        adjudications=adjudication_list,
    )
    knockout_breakdown = _score_knockout(
        model,
        prediction_data,
        result_list,
        active_rules,
        adjudications=adjudication_list,
    )
    group_points = int(group_breakdown["points"])
    knockout_points = int(knockout_breakdown["points"])
    return ScoreBreakdown(
        total_points=group_points + knockout_points,
        group_points=group_points,
        knockout_points=knockout_points,
        details={
            "version": SCORING_VERSION,
            "groups": group_breakdown,
            "knockout": knockout_breakdown,
        },
    )


def actual_tournament_data(
    model: TournamentModel,
    results: Iterable[MatchResult],
    *,
    adjudications: Iterable[StandingAdjudication] = (),
) -> dict[str, Any]:
    result_list = list(results)
    adjudication_list = tuple(adjudications)
    standings = compute_group_standings(
        model,
        result_list,
        adjudications=adjudication_list,
    )
    rankings = {
        group_id: [standing.team_id for standing in rows]
        for group_id, rows in standings.items()
        if _group_is_complete(model, group_id, rows)
    }
    if len(rankings) != len(model.groups):
        return {
            "group_rankings": rankings,
            "third_place_qualifier_team_ids": [],
            "seeded_round_of_32": [],
            "knockout": {},
        }
    qualifiers = best_third_place_qualifiers(
        model,
        standings,
        adjudications=adjudication_list,
    )
    return {
        "group_rankings": rankings,
        "third_place_qualifier_team_ids": list(qualifiers),
        "seeded_round_of_32": [],
        "knockout": _actual_knockout_data(
            model,
            result_list,
            qualifiers,
            adjudications=adjudication_list,
        ),
    }


def _score_groups(
    model: TournamentModel,
    prediction_data: Mapping[str, Any],
    results: list[MatchResult],
    rules: ScoringRules,
    *,
    adjudications: tuple[StandingAdjudication, ...],
) -> dict[str, Any]:
    standings = compute_group_standings(
        model,
        results,
        adjudications=adjudications,
    )
    actual_rankings = {
        group_id: [standing.team_id for standing in rows]
        for group_id, rows in standings.items()
        if _group_is_complete(model, group_id, rows)
    }
    all_groups_complete = len(actual_rankings) == len(model.groups)
    actual_thirds = (
        set(best_third_place_qualifiers(model, standings, adjudications=adjudications))
        if all_groups_complete
        else set()
    )
    predicted_rankings = _rankings(prediction_data)
    predicted_third_qualifiers = set(_strings(prediction_data.get("third_place_qualifier_team_ids")))

    group_items: list[dict[str, Any]] = []
    points = 0
    for group in model.groups:
        predicted = predicted_rankings.get(group.id, [])
        actual = actual_rankings.get(group.id, [])
        complete = group.id in actual_rankings
        winner_correct = complete and len(predicted) > 0 and len(actual) > 0 and predicted[0] == actual[0]
        runner_up_correct = complete and len(predicted) > 1 and len(actual) > 1 and predicted[1] == actual[1]
        winner_points = rules.group_winner if winner_correct else 0
        runner_up_points = rules.group_runner_up if runner_up_correct else 0
        points += winner_points + runner_up_points
        group_items.append(
            {
                "group_id": group.id,
                "complete": complete,
                "predicted_winner": predicted[0] if len(predicted) > 0 else None,
                "actual_winner": actual[0] if len(actual) > 0 else None,
                "winner_points": winner_points,
                "predicted_runner_up": predicted[1] if len(predicted) > 1 else None,
                "actual_runner_up": actual[1] if len(actual) > 1 else None,
                "runner_up_points": runner_up_points,
            }
        )

    third_place_hits = sorted(predicted_third_qualifiers & actual_thirds)
    third_place_points = len(third_place_hits) * rules.group_third_place_qualifier
    points += third_place_points
    return {
        "points": points,
        "group_positions": group_items,
        "third_place_qualifier_hits": third_place_hits,
        "third_place_qualifier_points": third_place_points,
    }


def _score_knockout(
    model: TournamentModel,
    prediction_data: Mapping[str, Any],
    results: list[MatchResult],
    rules: ScoringRules,
    *,
    adjudications: tuple[StandingAdjudication, ...],
) -> dict[str, Any]:
    actual_data = actual_tournament_data(
        model,
        results,
        adjudications=adjudications,
    )
    predicted_advancement = _advancement_by_round(model, prediction_data)
    actual_advancement = _advancement_by_round(model, actual_data)
    round_values = {
        "round_of_32": rules.round_of_32_advancement,
        "round_of_16": rules.round_of_16_advancement,
        "quarter_finals": rules.quarter_final_advancement,
        "semi_finals": rules.semi_final_advancement,
        "final": rules.final_advancement,
    }

    points = 0
    rounds: list[dict[str, Any]] = []
    for round_name, value in round_values.items():
        hits = sorted(predicted_advancement.get(round_name, set()) & actual_advancement.get(round_name, set()))
        round_points = len(hits) * value
        points += round_points
        rounds.append(
            {
                "round": round_name,
                "hits": hits,
                "points": round_points,
            }
        )

    predicted_final = _final_placements(model, prediction_data)
    actual_final = _final_placements(model, actual_data)
    placement_points = 0
    third_place_points = 0
    champion_points = 0
    runner_up_points = 0
    if predicted_final.get("third_place") == actual_final.get("third_place"):
        third_place_points = rules.third_place_winner
    if predicted_final.get("champion") == actual_final.get("champion"):
        champion_points = rules.champion
    if predicted_final.get("runner_up") == actual_final.get("runner_up"):
        runner_up_points = rules.runner_up
    placement_points = third_place_points + champion_points + runner_up_points
    points += placement_points

    return {
        "points": points,
        "advancement": rounds,
        "placements": {
            "predicted": predicted_final,
            "actual": actual_final,
            "third_place_points": third_place_points,
            "champion_points": champion_points,
            "runner_up_points": runner_up_points,
        },
    }


def _actual_knockout_data(
    model: TournamentModel,
    results: list[MatchResult],
    third_place_qualifier_team_ids: tuple[str, ...],
    *,
    adjudications: tuple[StandingAdjudication, ...],
) -> dict[str, list[dict[str, Any]]]:
    data = {
        "group_rankings": actual_group_rankings(
            model,
            results,
            adjudications=adjudications,
        ),
        "third_place_qualifier_team_ids": list(third_place_qualifier_team_ids),
        "seeded_round_of_32": [],
        "knockout": {},
    }
    winners_by_match_id = {
        result.match_id: result.winner_team_id
        for result in results
        if result.stage == "knockout" and result.winner_team_id and result.is_finished
    }
    for round_name in ROUND_ORDER:
        matches = get_round_matches(model, data, round_name)
        if not matches:
            continue
        entries: list[dict[str, Any]] = []
        for match in matches:
            winner = winners_by_match_id.get(match.id)
            if winner not in {match.home_team_id, match.away_team_id}:
                continue
            entries.append(
                {
                    "match_id": match.id,
                    "home_team_id": match.home_team_id,
                    "away_team_id": match.away_team_id,
                    "winner_team_id": winner,
                }
            )
        if entries:
            data["knockout"][round_name] = entries
    return data["knockout"]


def _group_is_complete(
    model: TournamentModel,
    group_id: str,
    standings: tuple[object, ...],
) -> bool:
    group = model.groups_by_id.get(group_id)
    if group is None:
        return False
    expected_played = len(group.team_ids) - 1
    return all(
        getattr(row, "played", 0) == expected_played
        for row in standings
    )


def _advancement_by_round(
    model: TournamentModel,
    data: Mapping[str, Any],
) -> dict[str, set[str]]:
    advancement: dict[str, set[str]] = {}
    for round_name in ROUND_ORDER:
        try:
            matches = get_round_matches(model, data, round_name)
        except PredictionValidationError:
            continue
        team_ids = {
            team_id
            for match in matches
            for team_id in (match.home_team_id, match.away_team_id)
            if team_id
        }
        if team_ids:
            advancement[round_name] = team_ids
    return advancement


def _final_placements(
    model: TournamentModel,
    data: Mapping[str, Any],
) -> dict[str, str | None]:
    try:
        final = get_round_matches(model, data, "final")
        third_place = get_round_matches(model, data, "third_place")
    except PredictionValidationError:
        return {"champion": None, "runner_up": None, "third_place": None}

    champion = final[0].winner_team_id if len(final) == 1 else None
    runner_up = final[0].loser_team_id if len(final) == 1 else None
    third = third_place[0].winner_team_id if len(third_place) == 1 else None
    return {
        "champion": champion,
        "runner_up": runner_up,
        "third_place": third,
    }


def _rankings(data: Mapping[str, Any]) -> dict[str, list[str]]:
    raw_rankings = data.get("group_rankings")
    if not isinstance(raw_rankings, Mapping):
        return {}
    return {
        str(group_id): _strings(team_ids)
        for group_id, team_ids in raw_rankings.items()
    }


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _positive_int(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and value >= 0 else fallback
