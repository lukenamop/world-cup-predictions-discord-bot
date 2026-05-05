from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from world_cup_bot.domain.tournament import DEFAULT_2026_FORMAT, TournamentFormat


ROUND_ORDER = (
    "round_of_32",
    "round_of_16",
    "quarter_finals",
    "semi_finals",
    "third_place",
    "final",
)
ROUND_LABELS = {
    "round_of_32": "Round of 32",
    "round_of_16": "Round of 16",
    "quarter_finals": "Quarter-finals",
    "semi_finals": "Semi-finals",
    "third_place": "Third-place match",
    "final": "Final",
}


class PredictionValidationError(ValueError):
    """Raised when a prediction step would create an invalid bracket."""


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    short_name: str
    country_code: str | None = None


@dataclass(frozen=True)
class Group:
    id: str
    label: str
    team_ids: tuple[str, ...]


@dataclass(frozen=True)
class RoundMatch:
    id: str
    home_team_id: str
    away_team_id: str
    winner_team_id: str | None = None

    @property
    def loser_team_id(self) -> str | None:
        if self.winner_team_id == self.home_team_id:
            return self.away_team_id
        if self.winner_team_id == self.away_team_id:
            return self.home_team_id
        return None


@dataclass(frozen=True)
class PredictionStep:
    kind: Literal["group_pick", "third_place", "knockout", "submit"]
    title: str
    description: str
    options: tuple[Team, ...]
    group_id: str | None = None
    rank_position: int | None = None
    round_name: str | None = None
    match_id: str | None = None
    min_values: int = 1
    max_values: int = 1


@dataclass(frozen=True)
class PredictionProgress:
    completed: int
    total: int


@dataclass(frozen=True)
class PredictionSummary:
    champion_team_id: str
    runner_up_team_id: str
    third_place_team_id: str
    fourth_place_team_id: str


@dataclass(frozen=True)
class TournamentModel:
    tournament_id: str
    name: str
    format: TournamentFormat
    teams: tuple[Team, ...]
    groups: tuple[Group, ...]
    round_of_32: tuple[Mapping[str, Any], ...]
    third_place_rules: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> TournamentModel:
        tournament = _mapping(config.get("tournament"))
        raw_format = _mapping(config.get("format"))
        tournament_format = TournamentFormat(
            group_count=_int(raw_format.get("group_count"), DEFAULT_2026_FORMAT.group_count),
            teams_per_group=_int(
                raw_format.get("teams_per_group"),
                DEFAULT_2026_FORMAT.teams_per_group,
            ),
            third_place_qualifiers=_int(
                raw_format.get("third_place_qualifiers"),
                DEFAULT_2026_FORMAT.third_place_qualifiers,
            ),
            opening_knockout_matches=_int(
                raw_format.get("opening_knockout_matches"),
                DEFAULT_2026_FORMAT.opening_knockout_matches,
            ),
        )
        teams = tuple(
            Team(
                id=str(raw_team["id"]),
                name=str(raw_team["name"]),
                short_name=str(raw_team.get("short_name") or raw_team["name"]),
                country_code=(
                    str(raw_team["country_code"])
                    if raw_team.get("country_code")
                    else None
                ),
            )
            for raw_team in _mappings(config.get("teams"))
        )
        groups = tuple(
            Group(
                id=str(raw_group["id"]),
                label=str(raw_group["label"]),
                team_ids=tuple(str(team_id) for team_id in raw_group["team_ids"]),
            )
            for raw_group in _mappings(config.get("groups"))
        )
        bracket = _mapping(config.get("bracket"))
        allocation = _mapping(config.get("third_place_allocation"))
        return cls(
            tournament_id=str(tournament.get("id") or ""),
            name=str(tournament.get("name") or ""),
            format=tournament_format,
            teams=teams,
            groups=groups,
            round_of_32=tuple(_mappings(bracket.get("round_of_32"))),
            third_place_rules=tuple(_mappings(allocation.get("rules"))),
        )

    @property
    def teams_by_id(self) -> dict[str, Team]:
        return {team.id: team for team in self.teams}

    @property
    def groups_by_id(self) -> dict[str, Group]:
        return {group.id: group for group in self.groups}

    def team(self, team_id: str) -> Team:
        try:
            return self.teams_by_id[team_id]
        except KeyError as exc:
            raise PredictionValidationError(f"Unknown team: {team_id}") from exc


def empty_prediction_data() -> dict[str, Any]:
    return {
        "group_rankings": {},
        "third_place_qualifier_team_ids": [],
        "seeded_round_of_32": [],
        "knockout": {},
    }


def restart_prediction_data() -> dict[str, Any]:
    return empty_prediction_data()


def undo_last_prediction_step(
    model: TournamentModel,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    updated = _copy_data(data)
    knockout = _knockout(updated)
    for round_name in reversed(ROUND_ORDER):
        entries = list(_round_entries(updated, round_name))
        if not entries:
            continue
        entries.pop()
        if entries:
            knockout[round_name] = [dict(entry) for entry in entries]
        else:
            knockout.pop(round_name, None)
        _clear_after_round(updated, round_name)
        return updated

    if _third_place_team_ids(updated):
        _clear_after_groups(updated)
        return updated

    rankings = _group_rankings(updated)
    for group in reversed(model.groups):
        ranking = list(rankings.get(group.id, []))
        if not ranking:
            continue
        ranking.pop()
        if ranking:
            rankings[group.id] = ranking
        else:
            rankings.pop(group.id, None)
        _clear_after_groups(updated)
        return updated

    raise PredictionValidationError("There is no previous prediction step yet.")


def record_group_pick(
    model: TournamentModel,
    data: Mapping[str, Any],
    *,
    group_id: str,
    team_id: str,
) -> dict[str, Any]:
    group = _group(model, group_id)
    if team_id not in group.team_ids:
        raise PredictionValidationError("Pick a team from this group.")

    updated = _copy_data(data)
    rankings = _group_rankings(updated)
    ranking = list(rankings.get(group_id, []))
    if len(ranking) >= len(group.team_ids):
        raise PredictionValidationError("This group ranking is already complete.")
    if team_id in ranking:
        raise PredictionValidationError("Each team can appear only once in a group ranking.")

    ranking.append(team_id)
    rankings[group_id] = ranking
    _clear_after_groups(updated)
    return updated


def record_third_place_qualifiers(
    model: TournamentModel,
    data: Mapping[str, Any],
    *,
    team_ids: Sequence[str],
) -> dict[str, Any]:
    _validate_group_rankings_complete(model, data)
    predicted_thirds = predicted_third_place_by_group(model, data)
    valid_third_ids = set(predicted_thirds.values())
    selected = [str(team_id) for team_id in team_ids]

    if len(selected) != model.format.third_place_qualifiers:
        raise PredictionValidationError(
            f"Pick exactly {model.format.third_place_qualifiers} third-place qualifiers."
        )
    if len(set(selected)) != len(selected):
        raise PredictionValidationError("Each third-place qualifier can be selected once.")
    unknown = sorted(set(selected) - valid_third_ids)
    if unknown:
        raise PredictionValidationError(
            "Third-place qualifier picks must be teams predicted third in their groups."
        )

    updated = _copy_data(data)
    updated["third_place_qualifier_team_ids"] = selected
    updated["seeded_round_of_32"] = [
        {
            "match_id": match.id,
            "home_team_id": match.home_team_id,
            "away_team_id": match.away_team_id,
        }
        for match in seed_round_of_32(model, updated)
    ]
    updated["knockout"] = {}
    return updated


def record_knockout_winner(
    model: TournamentModel,
    data: Mapping[str, Any],
    *,
    round_name: str,
    match_id: str,
    winner_team_id: str,
) -> dict[str, Any]:
    matches = get_round_matches(model, data, round_name)
    match_by_id = {match.id: match for match in matches}
    if match_id not in match_by_id:
        raise PredictionValidationError("That match is not ready for a pick yet.")
    match = match_by_id[match_id]
    if winner_team_id not in {match.home_team_id, match.away_team_id}:
        raise PredictionValidationError("Pick one of the teams in this match.")

    updated = _copy_data(data)
    knockout = _knockout(updated)
    entries = [
        entry
        for entry in _round_entries(updated, round_name)
        if entry.get("match_id") != match_id
    ]
    entries.append(
        {
            "match_id": match.id,
            "home_team_id": match.home_team_id,
            "away_team_id": match.away_team_id,
            "winner_team_id": winner_team_id,
        }
    )
    entry_by_match = {str(entry["match_id"]): entry for entry in entries}
    knockout[round_name] = [
        entry_by_match[current_match.id]
        for current_match in matches
        if current_match.id in entry_by_match
    ]
    _clear_after_round(updated, round_name)
    return updated


def next_prediction_step(
    model: TournamentModel,
    data: Mapping[str, Any],
) -> PredictionStep:
    rankings = _read_group_rankings(data)
    for group in model.groups:
        ranking = rankings.get(group.id, [])
        if len(ranking) < len(group.team_ids):
            picked = set(ranking)
            options = tuple(
                model.team(team_id)
                for team_id in group.team_ids
                if team_id not in picked
            )
            position = len(ranking) + 1
            return PredictionStep(
                kind="group_pick",
                title=f"{group.label}: pick #{position}",
                description="Rank this group from first through last.",
                options=options,
                group_id=group.id,
                rank_position=position,
            )

    if not _third_place_team_ids(data):
        options = tuple(
            model.team(team_id)
            for team_id in predicted_third_place_by_group(model, data).values()
        )
        return PredictionStep(
            kind="third_place",
            title="Pick advancing third-place teams",
            description=f"Choose {model.format.third_place_qualifiers} of your predicted third-place teams.",
            options=options,
            min_values=model.format.third_place_qualifiers,
            max_values=model.format.third_place_qualifiers,
        )

    for round_name in ROUND_ORDER:
        for match in get_round_matches(model, data, round_name):
            if match.winner_team_id is None:
                home = model.team(match.home_team_id).short_name
                away = model.team(match.away_team_id).short_name
                return PredictionStep(
                    kind="knockout",
                    title=f"{ROUND_LABELS[round_name]}: {home} vs {away}",
                    description="Pick the team that advances.",
                    options=(model.team(match.home_team_id), model.team(match.away_team_id)),
                    round_name=round_name,
                    match_id=match.id,
                )

    return PredictionStep(
        kind="submit",
        title="Ready to submit",
        description="Review the summary and submit when it looks right.",
        options=(),
    )


def prediction_progress(model: TournamentModel, data: Mapping[str, Any]) -> PredictionProgress:
    total = (
        len(model.groups) * model.format.teams_per_group
        + (1 if model.format.third_place_qualifiers else 0)
        + model.format.opening_knockout_matches * 2
    )
    completed_groups = sum(
        min(len(ranking), model.format.teams_per_group)
        for ranking in _read_group_rankings(data).values()
    )
    completed_thirds = 1 if _third_place_team_ids(data) else 0
    completed_knockout = sum(
        1
        for round_name in ROUND_ORDER
        for entry in _round_entries(data, round_name)
        if entry.get("winner_team_id")
    )
    return PredictionProgress(
        completed=completed_groups + completed_thirds + completed_knockout,
        total=total,
    )


def is_submission_complete(model: TournamentModel, data: Mapping[str, Any]) -> bool:
    try:
        prediction_summary(model, data)
    except PredictionValidationError:
        return False
    return True


def prediction_summary(
    model: TournamentModel,
    data: Mapping[str, Any],
) -> PredictionSummary:
    _validate_group_rankings_complete(model, data)
    if len(_third_place_team_ids(data)) != model.format.third_place_qualifiers:
        raise PredictionValidationError("Third-place qualifiers are incomplete.")

    final_matches = get_round_matches(model, data, "final")
    third_place_matches = get_round_matches(model, data, "third_place")
    if len(final_matches) != 1 or len(third_place_matches) != 1:
        raise PredictionValidationError("Knockout picks are incomplete.")

    final = final_matches[0]
    third_place = third_place_matches[0]
    if final.winner_team_id is None or final.loser_team_id is None:
        raise PredictionValidationError("Final pick is incomplete.")
    if third_place.winner_team_id is None or third_place.loser_team_id is None:
        raise PredictionValidationError("Third-place pick is incomplete.")

    return PredictionSummary(
        champion_team_id=final.winner_team_id,
        runner_up_team_id=final.loser_team_id,
        third_place_team_id=third_place.winner_team_id,
        fourth_place_team_id=third_place.loser_team_id,
    )


def predicted_third_place_by_group(
    model: TournamentModel,
    data: Mapping[str, Any],
) -> dict[str, str]:
    rankings = _read_group_rankings(data)
    thirds: dict[str, str] = {}
    for group in model.groups:
        ranking = rankings.get(group.id, [])
        if len(ranking) < 3:
            raise PredictionValidationError("Group rankings must include third place.")
        thirds[group.id] = ranking[2]
    return thirds


def seed_round_of_32(model: TournamentModel, data: Mapping[str, Any]) -> tuple[RoundMatch, ...]:
    _validate_group_rankings_complete(model, data)
    rankings = _read_group_rankings(data)
    predicted_thirds = predicted_third_place_by_group(model, data)
    selected_thirds = set(_third_place_team_ids(data))
    selected_groups = sorted(
        group_id
        for group_id, team_id in predicted_thirds.items()
        if team_id in selected_thirds
    )
    if len(selected_groups) != model.format.third_place_qualifiers:
        raise PredictionValidationError("Third-place qualifiers are incomplete.")

    allocation = _find_allocation_rule(model, selected_groups)
    matches: list[RoundMatch] = []
    for raw_match in model.round_of_32:
        home_team_id = _resolve_source(
            raw_match["home_source"],
            rankings,
            allocation,
            predicted_thirds,
        )
        away_team_id = _resolve_source(
            raw_match["away_source"],
            rankings,
            allocation,
            predicted_thirds,
        )
        matches.append(
            RoundMatch(
                id=str(raw_match["id"]),
                home_team_id=home_team_id,
                away_team_id=away_team_id,
            )
        )
    return tuple(matches)


def get_round_matches(
    model: TournamentModel,
    data: Mapping[str, Any],
    round_name: str,
) -> tuple[RoundMatch, ...]:
    if round_name not in ROUND_ORDER:
        raise PredictionValidationError(f"Unknown knockout round: {round_name}")
    if round_name == "round_of_32":
        seeded = _seeded_round_of_32(data)
        if not seeded:
            seeded = [
                {
                    "match_id": match.id,
                    "home_team_id": match.home_team_id,
                    "away_team_id": match.away_team_id,
                }
                for match in seed_round_of_32(model, data)
            ]
        return _with_stored_winners(data, round_name, seeded)
    if round_name == "final":
        semi_finals = get_round_matches(model, data, "semi_finals")
        if len(semi_finals) != 2 or any(
            match.winner_team_id is None for match in semi_finals
        ):
            return ()
        seeded = [
            {
                "match_id": "FINAL-1",
                "home_team_id": semi_finals[0].winner_team_id,
                "away_team_id": semi_finals[1].winner_team_id,
            }
        ]
        return _with_stored_winners(data, round_name, seeded)
    if round_name == "third_place":
        semi_finals = get_round_matches(model, data, "semi_finals")
        if len(semi_finals) != 2 or any(match.loser_team_id is None for match in semi_finals):
            return ()
        seeded = [
            {
                "match_id": "THIRD-1",
                "home_team_id": semi_finals[0].loser_team_id,
                "away_team_id": semi_finals[1].loser_team_id,
            }
        ]
        return _with_stored_winners(data, round_name, seeded)

    previous_round = ROUND_ORDER[ROUND_ORDER.index(round_name) - 1]
    previous_matches = get_round_matches(model, data, previous_round)
    if any(match.winner_team_id is None for match in previous_matches):
        return ()
    winners = [match.winner_team_id for match in previous_matches]
    if len(winners) % 2:
        return ()
    seeded = [
        {
            "match_id": f"{_round_prefix(round_name)}-{index + 1}",
            "home_team_id": winners[index],
            "away_team_id": winners[index + 1],
        }
        for index in range(0, len(winners), 2)
    ]
    return _with_stored_winners(data, round_name, seeded)


def _with_stored_winners(
    data: Mapping[str, Any],
    round_name: str,
    seeded: Sequence[Mapping[str, Any]],
) -> tuple[RoundMatch, ...]:
    entries = {
        (
            str(entry.get("match_id")),
            str(entry.get("home_team_id")),
            str(entry.get("away_team_id")),
        ): str(entry.get("winner_team_id"))
        for entry in _round_entries(data, round_name)
        if entry.get("winner_team_id")
    }
    return tuple(
        RoundMatch(
            id=str(match["match_id"]),
            home_team_id=str(match["home_team_id"]),
            away_team_id=str(match["away_team_id"]),
            winner_team_id=entries.get(
                (
                    str(match["match_id"]),
                    str(match["home_team_id"]),
                    str(match["away_team_id"]),
                )
            ),
        )
        for match in seeded
    )


def _find_allocation_rule(
    model: TournamentModel,
    selected_groups: Sequence[str],
) -> Mapping[str, str]:
    selected = tuple(sorted(selected_groups))
    for rule in model.third_place_rules:
        qualifying_groups = tuple(sorted(str(group_id) for group_id in rule["qualifying_groups"]))
        if qualifying_groups == selected:
            return {
                str(slot_id): str(group_id)
                for slot_id, group_id in _mapping(rule["slot_assignments"]).items()
            }
    raise PredictionValidationError(
        "No third-place allocation rule matches those qualifying groups."
    )


def _resolve_source(
    source: object,
    rankings: Mapping[str, Sequence[str]],
    allocation: Mapping[str, str],
    predicted_thirds: Mapping[str, str],
) -> str:
    raw_source = _mapping(source)
    source_type = raw_source.get("type")
    if source_type == "group_position":
        group_id = str(raw_source["group_id"])
        position = int(raw_source["position"])
        return str(rankings[group_id][position - 1])
    if source_type == "third_place_slot":
        group_id = allocation[str(raw_source["slot_id"])]
        return predicted_thirds[group_id]
    raise PredictionValidationError("Unsupported bracket source.")


def _validate_group_rankings_complete(
    model: TournamentModel,
    data: Mapping[str, Any],
) -> None:
    rankings = _read_group_rankings(data)
    for group in model.groups:
        ranking = rankings.get(group.id, [])
        if len(ranking) != len(group.team_ids):
            raise PredictionValidationError("Group rankings are incomplete.")
        if set(ranking) != set(group.team_ids):
            raise PredictionValidationError(f"{group.label} ranking has invalid teams.")


def _group(model: TournamentModel, group_id: str) -> Group:
    try:
        return model.groups_by_id[group_id]
    except KeyError as exc:
        raise PredictionValidationError(f"Unknown group: {group_id}") from exc


def _read_group_rankings(data: Mapping[str, Any]) -> dict[str, list[str]]:
    raw_rankings = _mapping(data.get("group_rankings"))
    return {
        str(group_id): [str(team_id) for team_id in team_ids]
        for group_id, team_ids in raw_rankings.items()
        if isinstance(team_ids, list)
    }


def _group_rankings(data: dict[str, Any]) -> dict[str, list[str]]:
    if not isinstance(data.get("group_rankings"), dict):
        data["group_rankings"] = {}
    return data["group_rankings"]


def _third_place_team_ids(data: Mapping[str, Any]) -> list[str]:
    raw_team_ids = data.get("third_place_qualifier_team_ids")
    if not isinstance(raw_team_ids, list):
        return []
    return [str(team_id) for team_id in raw_team_ids]


def _seeded_round_of_32(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _mappings(data.get("seeded_round_of_32"))


def _round_entries(data: Mapping[str, Any], round_name: str) -> list[Mapping[str, Any]]:
    return _mappings(_mapping(data.get("knockout")).get(round_name))


def _knockout(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(data.get("knockout"), dict):
        data["knockout"] = {}
    return data["knockout"]


def _copy_data(data: Mapping[str, Any]) -> dict[str, Any]:
    copied = deepcopy(dict(data))
    if not copied:
        return empty_prediction_data()
    copied.setdefault("group_rankings", {})
    copied.setdefault("third_place_qualifier_team_ids", [])
    copied.setdefault("seeded_round_of_32", [])
    copied.setdefault("knockout", {})
    return copied


def _clear_after_groups(data: dict[str, Any]) -> None:
    data["third_place_qualifier_team_ids"] = []
    data["seeded_round_of_32"] = []
    data["knockout"] = {}


def _clear_after_round(data: dict[str, Any], round_name: str) -> None:
    knockout = _knockout(data)
    round_index = ROUND_ORDER.index(round_name)
    for later_round in ROUND_ORDER[round_index + 1 :]:
        knockout.pop(later_round, None)


def _round_prefix(round_name: str) -> str:
    return {
        "round_of_16": "R16",
        "quarter_finals": "QF",
        "semi_finals": "SF",
    }[round_name]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mappings(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _int(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and value > 0 else fallback
