from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal, Sequence

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


TieScope = Literal["group", "best_third"]


@dataclass(frozen=True)
class StandingAdjudication:
    scope: TieScope
    ordered_team_ids: tuple[str, ...]
    reason: str
    group_id: str | None = None
    criterion: str = "operator_adjudication"


@dataclass(frozen=True)
class UnresolvedTie:
    scope: TieScope
    team_ids: tuple[str, ...]
    criterion: str
    group_id: str | None = None

    def describe(self) -> str:
        prefix = (
            f"group {self.group_id}"
            if self.scope == "group" and self.group_id
            else "best third-place ranking"
        )
        return (
            f"Unresolved {prefix} tie after {self.criterion}: "
            f"{', '.join(self.team_ids)}"
        )


class StandingResolutionError(RuntimeError):
    def __init__(self, unresolved_ties: Sequence[UnresolvedTie]) -> None:
        self.unresolved_ties = tuple(unresolved_ties)
        super().__init__("; ".join(tie.describe() for tie in self.unresolved_ties))


def compute_group_standings(
    model: TournamentModel,
    results: Iterable[MatchResult],
    *,
    adjudications: Iterable[StandingAdjudication] = (),
) -> dict[str, tuple[TeamStanding, ...]]:
    adjudication_list = tuple(adjudications)
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

        group_result_list = results_by_group.get(group.id, [])
        ordered = _order_group_rows(
            tuple(rows.values()),
            group_result_list,
            adjudications=adjudication_list,
            complete=_group_rows_are_complete(tuple(rows.values())),
        )
        standings[group.id] = tuple(ordered)

    return standings


def actual_group_rankings(
    model: TournamentModel,
    results: Iterable[MatchResult],
    *,
    adjudications: Iterable[StandingAdjudication] = (),
) -> dict[str, list[str]]:
    return {
        group_id: [standing.team_id for standing in standings]
        for group_id, standings in compute_group_standings(
            model,
            results,
            adjudications=adjudications,
        ).items()
    }


def best_third_place_qualifiers(
    model: TournamentModel,
    group_standings: dict[str, tuple[TeamStanding, ...]],
    *,
    adjudications: Iterable[StandingAdjudication] = (),
) -> tuple[str, ...]:
    third_place_rows = [
        standings[2]
        for group_id, standings in group_standings.items()
        if group_id in model.groups_by_id and len(standings) >= 3
    ]
    qualifier_count = model.format.third_place_qualifiers
    if len(third_place_rows) <= qualifier_count:
        return tuple(row.team_id for row in third_place_rows)
    ordered = _order_best_third_rows(
        tuple(third_place_rows),
        adjudications=tuple(adjudications),
        needed_count=qualifier_count,
    )
    return tuple(
        row.team_id
        for row in ordered[:qualifier_count]
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


def _order_group_rows(
    rows: tuple[TeamStanding, ...],
    group_results: list[MatchResult],
    *,
    adjudications: tuple[StandingAdjudication, ...],
    complete: bool,
) -> tuple[TeamStanding, ...]:
    if not complete:
        return tuple(sorted(rows, key=_partial_group_sort_key))
    ordered: list[TeamStanding] = []
    for _, tied_rows in _partitions(rows, key=lambda row: row.points).items():
        if len(tied_rows) == 1:
            ordered.extend(tied_rows)
            continue
        ordered.extend(
            _resolve_group_tie(
                tuple(tied_rows),
                group_results,
                adjudications=adjudications,
                criterion_index=0,
            )
        )
    return tuple(ordered)


def _resolve_group_tie(
    rows: tuple[TeamStanding, ...],
    group_results: list[MatchResult],
    *,
    adjudications: tuple[StandingAdjudication, ...],
    criterion_index: int,
) -> tuple[TeamStanding, ...]:
    criteria = (
        ("head_to_head_points", lambda current: _head_to_head_values(current, group_results, "points")),
        (
            "head_to_head_goal_difference",
            lambda current: _head_to_head_values(current, group_results, "goal_difference"),
        ),
        (
            "head_to_head_goals_for",
            lambda current: _head_to_head_values(current, group_results, "goals_for"),
        ),
        ("overall_goal_difference", lambda current: {row.team_id: row.goal_difference for row in current}),
        ("overall_goals_for", lambda current: {row.team_id: row.goals_for for row in current}),
    )
    current = rows
    for index in range(criterion_index, len(criteria)):
        _, values_for = criteria[index]
        values = values_for(current)
        partitions = _partitions(current, key=lambda row: values[row.team_id])
        if len(partitions) == 1:
            continue
        ordered: list[TeamStanding] = []
        for _, tied_rows in partitions.items():
            if len(tied_rows) == 1:
                ordered.extend(tied_rows)
            else:
                ordered.extend(
                    _resolve_group_tie(
                        tuple(tied_rows),
                        group_results,
                        adjudications=adjudications,
                        criterion_index=index + 1,
                    )
                )
        return tuple(ordered)

    adjudicated = _apply_adjudication(
        current,
        adjudications=adjudications,
        scope="group",
        group_id=current[0].group_id,
    )
    if adjudicated is not None:
        return adjudicated
    raise StandingResolutionError(
        [
            UnresolvedTie(
                scope="group",
                group_id=current[0].group_id,
                team_ids=tuple(row.team_id for row in sorted(current, key=lambda row: row.team_id)),
                criterion="team conduct score or FIFA ranking",
            )
        ]
    )


def _order_best_third_rows(
    rows: tuple[TeamStanding, ...],
    *,
    adjudications: tuple[StandingAdjudication, ...],
    needed_count: int,
) -> tuple[TeamStanding, ...]:
    ordered = _resolve_best_third_tie(
        rows,
        adjudications=adjudications,
        criterion_index=0,
        needed_count=needed_count,
    )
    return tuple(ordered)


def _resolve_best_third_tie(
    rows: tuple[TeamStanding, ...],
    *,
    adjudications: tuple[StandingAdjudication, ...],
    criterion_index: int,
    needed_count: int,
) -> tuple[TeamStanding, ...]:
    criteria = (
        ("points", lambda current: {row.team_id: row.points for row in current}),
        ("goal_difference", lambda current: {row.team_id: row.goal_difference for row in current}),
        ("goals_for", lambda current: {row.team_id: row.goals_for for row in current}),
    )
    current = rows
    for index in range(criterion_index, len(criteria)):
        _, values_for = criteria[index]
        values = values_for(current)
        partitions = _partitions(current, key=lambda row: values[row.team_id])
        if len(partitions) == 1:
            continue
        ordered: list[TeamStanding] = []
        for _, tied_rows in partitions.items():
            tied_count = len(tied_rows)
            if (
                tied_count == 1
                or len(ordered) >= needed_count
                or len(ordered) + tied_count <= needed_count
            ):
                ordered.extend(tied_rows)
            else:
                ordered.extend(
                    _resolve_best_third_tie(
                        tuple(tied_rows),
                        adjudications=adjudications,
                        criterion_index=index + 1,
                        needed_count=needed_count - len(ordered),
                    )
                )
        return tuple(ordered)

    adjudicated = _apply_adjudication(
        current,
        adjudications=adjudications,
        scope="best_third",
        group_id=None,
    )
    if adjudicated is not None:
        return adjudicated
    raise StandingResolutionError(
        [
            UnresolvedTie(
                scope="best_third",
                group_id=None,
                team_ids=tuple(row.team_id for row in sorted(current, key=lambda row: row.team_id)),
                criterion="team conduct score or FIFA ranking",
            )
        ]
    )


def _head_to_head_values(
    rows: tuple[TeamStanding, ...],
    group_results: list[MatchResult],
    value_name: Literal["points", "goal_difference", "goals_for"],
) -> dict[str, int]:
    tied_team_ids = {row.team_id for row in rows}
    values: dict[str, int] = {}
    for row in rows:
        head_to_head = _head_to_head_row(row.team_id, tied_team_ids, group_results)
        values[row.team_id] = int(getattr(head_to_head, value_name))
    return values


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


def _apply_adjudication(
    rows: tuple[TeamStanding, ...],
    *,
    adjudications: tuple[StandingAdjudication, ...],
    scope: TieScope,
    group_id: str | None,
) -> tuple[TeamStanding, ...] | None:
    row_by_team = {row.team_id: row for row in rows}
    tied_team_ids = frozenset(row_by_team)
    for adjudication in reversed(adjudications):
        if adjudication.scope != scope:
            continue
        if adjudication.group_id != group_id:
            continue
        ordered = [
            team_id
            for team_id in adjudication.ordered_team_ids
            if team_id in tied_team_ids
        ]
        if frozenset(ordered) != tied_team_ids:
            continue
        return tuple(row_by_team[team_id] for team_id in ordered)
    return None


def _partitions(
    rows: tuple[TeamStanding, ...],
    *,
    key: Callable[[TeamStanding], int],
) -> dict[int, list[TeamStanding]]:
    partitions: dict[int, list[TeamStanding]] = {}
    for row in rows:
        value = key(row)
        partitions.setdefault(int(value), []).append(row)
    return {
        value: sorted(partitions[value], key=lambda row: row.team_id)
        for value in sorted(partitions, reverse=True)
    }


def _partial_group_sort_key(row: TeamStanding) -> tuple[int, int, int, int, str]:
    return (
        -row.points,
        -row.goal_difference,
        -row.goals_for,
        -row.wins,
        row.team_id,
    )


def _group_rows_are_complete(rows: tuple[TeamStanding, ...]) -> bool:
    if not rows:
        return False
    expected_played = len(rows) - 1
    return all(row.played == expected_played for row in rows)
