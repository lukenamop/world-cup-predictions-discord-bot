from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from world_cup_bot.domain.predictions import TournamentModel


FINISHED_STATUSES = frozenset({"FINISHED", "AWARDED"})


@dataclass(frozen=True)
class MatchResult:
    match_id: str
    stage: str
    home_team_id: str
    away_team_id: str
    status: str
    home_score: int | None = None
    away_score: int | None = None
    group_id: str | None = None
    round_name: str | None = None
    winner_team_id: str | None = None
    played_at: datetime | None = None

    @property
    def is_finished(self) -> bool:
        return (
            self.status in FINISHED_STATUSES
            and self.home_score is not None
            and self.away_score is not None
        )


@dataclass(frozen=True)
class TeamStanding:
    group_id: str
    team_id: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def compute_group_standings(
    model: TournamentModel,
    results: Iterable[MatchResult],
) -> dict[str, tuple[TeamStanding, ...]]:
    group_results = [
        result
        for result in results
        if result.stage == "group" and result.group_id and result.is_finished
    ]
    results_by_group: dict[str, list[MatchResult]] = {}
    for result in group_results:
        results_by_group.setdefault(str(result.group_id), []).append(result)

    standings: dict[str, tuple[TeamStanding, ...]] = {}
    for group in model.groups:
        rows = {
            team_id: _blank_standing(group.id, team_id)
            for team_id in group.team_ids
        }
        for result in results_by_group.get(group.id, []):
            if result.home_team_id not in rows or result.away_team_id not in rows:
                continue
            rows[result.home_team_id] = _apply_result(
                rows[result.home_team_id],
                goals_for=result.home_score or 0,
                goals_against=result.away_score or 0,
            )
            rows[result.away_team_id] = _apply_result(
                rows[result.away_team_id],
                goals_for=result.away_score or 0,
                goals_against=result.home_score or 0,
            )

        ordered = sorted(
            rows.values(),
            key=lambda row: _standing_sort_key(row, results_by_group.get(group.id, [])),
        )
        standings[group.id] = tuple(ordered)

    return standings


def actual_group_rankings(
    model: TournamentModel,
    results: Iterable[MatchResult],
) -> dict[str, list[str]]:
    return {
        group_id: [standing.team_id for standing in standings]
        for group_id, standings in compute_group_standings(model, results).items()
    }


def best_third_place_qualifiers(
    model: TournamentModel,
    group_standings: dict[str, tuple[TeamStanding, ...]],
) -> tuple[str, ...]:
    third_place_rows = [
        standings[2]
        for group_id, standings in group_standings.items()
        if group_id in model.groups_by_id and len(standings) >= 3
    ]
    ordered = sorted(
        third_place_rows,
        key=lambda row: (
            -row.points,
            -row.goal_difference,
            -row.goals_for,
            -row.wins,
            row.team_id,
        ),
    )
    return tuple(
        row.team_id
        for row in ordered[: model.format.third_place_qualifiers]
    )


def _blank_standing(group_id: str, team_id: str) -> TeamStanding:
    return TeamStanding(
        group_id=group_id,
        team_id=team_id,
        played=0,
        wins=0,
        draws=0,
        losses=0,
        goals_for=0,
        goals_against=0,
        points=0,
    )


def _apply_result(
    row: TeamStanding,
    *,
    goals_for: int,
    goals_against: int,
) -> TeamStanding:
    win = goals_for > goals_against
    draw = goals_for == goals_against
    return TeamStanding(
        group_id=row.group_id,
        team_id=row.team_id,
        played=row.played + 1,
        wins=row.wins + (1 if win else 0),
        draws=row.draws + (1 if draw else 0),
        losses=row.losses + (1 if goals_for < goals_against else 0),
        goals_for=row.goals_for + goals_for,
        goals_against=row.goals_against + goals_against,
        points=row.points + (3 if win else 1 if draw else 0),
    )


def _standing_sort_key(
    row: TeamStanding,
    group_results: list[MatchResult],
) -> tuple[int, int, int, int, int, int, int, str]:
    tied_team_ids = _initially_tied_team_ids(row, group_results)
    head_to_head = _head_to_head_row(row.team_id, tied_team_ids, group_results)
    return (
        -row.points,
        -row.goal_difference,
        -row.goals_for,
        -head_to_head.points,
        -head_to_head.goal_difference,
        -head_to_head.goals_for,
        -row.wins,
        row.team_id,
    )


def _initially_tied_team_ids(
    row: TeamStanding,
    group_results: list[MatchResult],
) -> set[str]:
    all_rows: dict[str, TeamStanding] = {}
    for result in group_results:
        all_rows.setdefault(
            result.home_team_id,
            _blank_standing(row.group_id, result.home_team_id),
        )
        all_rows.setdefault(
            result.away_team_id,
            _blank_standing(row.group_id, result.away_team_id),
        )
        all_rows[result.home_team_id] = _apply_result(
            all_rows[result.home_team_id],
            goals_for=result.home_score or 0,
            goals_against=result.away_score or 0,
        )
        all_rows[result.away_team_id] = _apply_result(
            all_rows[result.away_team_id],
            goals_for=result.away_score or 0,
            goals_against=result.home_score or 0,
        )

    return {
        other.team_id
        for other in all_rows.values()
        if (
            other.points,
            other.goal_difference,
            other.goals_for,
        )
        == (
            row.points,
            row.goal_difference,
            row.goals_for,
        )
    }


def _head_to_head_row(
    team_id: str,
    tied_team_ids: set[str],
    group_results: list[MatchResult],
) -> TeamStanding:
    row = _blank_standing("", team_id)
    if len(tied_team_ids) < 2:
        return row
    for result in group_results:
        if {result.home_team_id, result.away_team_id} - tied_team_ids:
            continue
        if team_id == result.home_team_id:
            row = _apply_result(
                row,
                goals_for=result.home_score or 0,
                goals_against=result.away_score or 0,
            )
        elif team_id == result.away_team_id:
            row = _apply_result(
                row,
                goals_for=result.away_score or 0,
                goals_against=result.home_score or 0,
            )
    return row
