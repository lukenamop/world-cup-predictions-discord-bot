from __future__ import annotations

from collections import Counter
from datetime import datetime
from itertools import combinations
from math import comb
from typing import Any, Mapping, Sequence

from world_cup_bot.domain.tournament import (
    DEFAULT_2026_FORMAT,
    TournamentFormat,
    TournamentSummary,
    TournamentValidationReport,
)


def validate_tournament_config(config: Mapping[str, Any]) -> TournamentValidationReport:
    errors: list[str] = []
    warnings: list[str] = []

    schema_version = _required_string(config, "schema_version", errors)
    tournament = _required_mapping(config, "tournament", errors)
    teams = _required_list(config, "teams", errors)
    groups = _required_list(config, "groups", errors)
    fixtures = _required_list(config, "fixtures", errors)
    bracket = _required_mapping(config, "bracket", errors)
    allocation = _required_mapping(config, "third_place_allocation", errors)

    if errors:
        return TournamentValidationReport(False, tuple(errors), tuple(warnings), None)

    tournament_id = _required_string(tournament, "id", errors, parent="tournament")
    tournament_name = _required_string(tournament, "name", errors, parent="tournament")
    tournament_format = _read_format(config.get("format"), errors)

    team_ids = _validate_teams(teams, errors)
    group_ids, group_team_ids = _validate_groups(
        groups,
        team_ids,
        tournament_format,
        errors,
    )
    _validate_fixtures(fixtures, group_team_ids, errors)
    third_place_slots, opening_knockout_matches = _validate_bracket(
        bracket,
        group_ids,
        tournament_format,
        errors,
    )
    source_version, allocation_rule_count = _validate_third_place_allocation(
        allocation,
        group_ids,
        third_place_slots,
        tournament_format,
        errors,
    )

    expected_team_count = tournament_format.group_count * tournament_format.teams_per_group
    if len(team_ids) != expected_team_count:
        errors.append(
            "teams must contain "
            f"{expected_team_count} teams for this tournament format; found {len(team_ids)}"
        )

    used_team_ids = {team_id for team_ids_for_group in group_team_ids.values() for team_id in team_ids_for_group}
    unused_team_ids = sorted(team_ids - used_team_ids)
    if unused_team_ids:
        errors.append(f"teams not assigned to a group: {', '.join(unused_team_ids)}")

    duplicated_group_teams = _duplicates(
        team_id for team_ids_for_group in group_team_ids.values() for team_id in team_ids_for_group
    )
    if duplicated_group_teams:
        errors.append(
            "teams assigned to multiple groups: " + ", ".join(duplicated_group_teams)
        )

    summary = None
    if not errors:
        summary = TournamentSummary(
            tournament_id=tournament_id,
            name=tournament_name,
            schema_version=schema_version,
            team_count=len(team_ids),
            group_count=len(group_ids),
            fixture_count=len(fixtures),
            opening_knockout_matches=opening_knockout_matches,
            third_place_rule_count=allocation_rule_count,
            source_version=source_version,
        )

    return TournamentValidationReport(
        valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        summary=summary,
    )


def _read_format(raw_format: object, errors: list[str]) -> TournamentFormat:
    if raw_format is None:
        return DEFAULT_2026_FORMAT
    if not isinstance(raw_format, Mapping):
        errors.append("format must be an object when provided")
        return DEFAULT_2026_FORMAT

    return TournamentFormat(
        group_count=_positive_int(raw_format, "group_count", errors, DEFAULT_2026_FORMAT.group_count),
        teams_per_group=_positive_int(raw_format, "teams_per_group", errors, DEFAULT_2026_FORMAT.teams_per_group),
        third_place_qualifiers=_positive_int(
            raw_format,
            "third_place_qualifiers",
            errors,
            DEFAULT_2026_FORMAT.third_place_qualifiers,
        ),
        opening_knockout_matches=_positive_int(
            raw_format,
            "opening_knockout_matches",
            errors,
            DEFAULT_2026_FORMAT.opening_knockout_matches,
        ),
    )


def _validate_teams(teams: Sequence[object], errors: list[str]) -> set[str]:
    team_ids: list[str] = []
    for index, raw_team in enumerate(teams):
        path = f"teams[{index}]"
        if not isinstance(raw_team, Mapping):
            errors.append(f"{path} must be an object")
            continue
        team_id = _required_string(raw_team, "id", errors, parent=path)
        _required_string(raw_team, "name", errors, parent=path)
        country_code = _required_string(raw_team, "country_code", errors, parent=path)
        if country_code and len(country_code) not in (2, 3):
            errors.append(f"{path}.country_code must be a 2- or 3-letter code")
        if team_id:
            team_ids.append(team_id)

    duplicates = _duplicates(team_ids)
    if duplicates:
        errors.append("duplicate team ids: " + ", ".join(duplicates))

    return set(team_ids)


def _validate_groups(
    groups: Sequence[object],
    team_ids: set[str],
    tournament_format: TournamentFormat,
    errors: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    group_ids: list[str] = []
    group_team_ids: dict[str, list[str]] = {}
    for index, raw_group in enumerate(groups):
        path = f"groups[{index}]"
        if not isinstance(raw_group, Mapping):
            errors.append(f"{path} must be an object")
            continue
        group_id = _required_string(raw_group, "id", errors, parent=path)
        _required_string(raw_group, "label", errors, parent=path)
        raw_team_ids = _required_list(raw_group, "team_ids", errors, parent=path)
        current_team_ids = [str(team_id) for team_id in raw_team_ids if isinstance(team_id, str)]

        if len(current_team_ids) != len(raw_team_ids):
            errors.append(f"{path}.team_ids must only contain strings")
        if len(current_team_ids) != tournament_format.teams_per_group:
            errors.append(
                f"{path}.team_ids must contain "
                f"{tournament_format.teams_per_group} teams; found {len(current_team_ids)}"
            )
        unknown = sorted(set(current_team_ids) - team_ids)
        if unknown:
            errors.append(f"{path}.team_ids references unknown teams: {', '.join(unknown)}")
        duplicated = _duplicates(current_team_ids)
        if duplicated:
            errors.append(f"{path}.team_ids contains duplicates: {', '.join(duplicated)}")
        if group_id:
            group_ids.append(group_id)
            group_team_ids[group_id] = current_team_ids

    duplicated_groups = _duplicates(group_ids)
    if duplicated_groups:
        errors.append("duplicate group ids: " + ", ".join(duplicated_groups))
    if len(group_ids) != tournament_format.group_count:
        errors.append(
            "groups must contain "
            f"{tournament_format.group_count} groups for this tournament format; "
            f"found {len(group_ids)}"
        )

    return group_ids, group_team_ids


def _validate_fixtures(
    fixtures: Sequence[object],
    group_team_ids: Mapping[str, list[str]],
    errors: list[str],
) -> None:
    fixture_ids: list[str] = []
    fixtures_by_group: dict[str, Counter[tuple[str, str]]] = {
        group_id: Counter() for group_id in group_team_ids
    }
    for index, raw_fixture in enumerate(fixtures):
        path = f"fixtures[{index}]"
        if not isinstance(raw_fixture, Mapping):
            errors.append(f"{path} must be an object")
            continue

        fixture_id = _required_string(raw_fixture, "id", errors, parent=path)
        stage = _required_string(raw_fixture, "stage", errors, parent=path)
        if stage != "group":
            errors.append(f"{path}.stage must be 'group' for Milestone 2 imports")
        group_id = _required_string(raw_fixture, "group_id", errors, parent=path)
        home_team_id = _required_string(raw_fixture, "home_team_id", errors, parent=path)
        away_team_id = _required_string(raw_fixture, "away_team_id", errors, parent=path)
        kickoff_utc = _required_string(raw_fixture, "kickoff_utc", errors, parent=path)

        if kickoff_utc:
            _validate_utc_datetime(kickoff_utc, f"{path}.kickoff_utc", errors)
        if fixture_id:
            fixture_ids.append(fixture_id)
        if group_id not in group_team_ids:
            errors.append(f"{path}.group_id references unknown group: {group_id}")
            continue

        group_teams = set(group_team_ids[group_id])
        unknown = sorted({home_team_id, away_team_id} - group_teams)
        if unknown:
            errors.append(
                f"{path} references team(s) outside group {group_id}: "
                + ", ".join(unknown)
            )
        if home_team_id == away_team_id:
            errors.append(f"{path} cannot use the same team twice")
        if home_team_id and away_team_id:
            fixtures_by_group[group_id][tuple(sorted((home_team_id, away_team_id)))] += 1

    duplicated_fixtures = _duplicates(fixture_ids)
    if duplicated_fixtures:
        errors.append("duplicate fixture ids: " + ", ".join(duplicated_fixtures))

    for group_id, team_ids in group_team_ids.items():
        expected_pairs = {tuple(sorted(pair)) for pair in combinations(team_ids, 2)}
        actual_pairs = set(fixtures_by_group[group_id])
        missing_pairs = sorted(expected_pairs - actual_pairs)
        extra_pairs = sorted(actual_pairs - expected_pairs)
        duplicate_pairs = sorted(
            pair for pair, count in fixtures_by_group[group_id].items() if count > 1
        )
        if missing_pairs:
            readable = ", ".join(f"{home} vs {away}" for home, away in missing_pairs)
            errors.append(f"fixtures missing group {group_id} matches: {readable}")
        if extra_pairs:
            readable = ", ".join(f"{home} vs {away}" for home, away in extra_pairs)
            errors.append(f"fixtures contain unexpected group {group_id} matches: {readable}")
        if duplicate_pairs:
            readable = ", ".join(f"{home} vs {away}" for home, away in duplicate_pairs)
            errors.append(f"fixtures duplicate group {group_id} matches: {readable}")


def _validate_bracket(
    bracket: Mapping[str, object],
    group_ids: Sequence[str],
    tournament_format: TournamentFormat,
    errors: list[str],
) -> tuple[list[str], int]:
    raw_round_of_32 = _required_list(bracket, "round_of_32", errors, parent="bracket")
    match_ids: list[str] = []
    group_position_slots: list[tuple[str, int]] = []
    third_place_slots: list[str] = []
    valid_group_ids = set(group_ids)

    for index, raw_match in enumerate(raw_round_of_32):
        path = f"bracket.round_of_32[{index}]"
        if not isinstance(raw_match, Mapping):
            errors.append(f"{path} must be an object")
            continue
        match_id = _required_string(raw_match, "id", errors, parent=path)
        home_source = _required_mapping(raw_match, "home_source", errors, parent=path)
        away_source = _required_mapping(raw_match, "away_source", errors, parent=path)
        if match_id:
            match_ids.append(match_id)
        home_group_positions, home_third_place_slots = _validate_bracket_source(
            home_source,
            f"{path}.home_source",
            valid_group_ids,
            errors,
        )
        away_group_positions, away_third_place_slots = _validate_bracket_source(
            away_source,
            f"{path}.away_source",
            valid_group_ids,
            errors,
        )
        group_position_slots.extend(home_group_positions)
        group_position_slots.extend(away_group_positions)
        third_place_slots.extend(home_third_place_slots)
        third_place_slots.extend(away_third_place_slots)

    duplicated_matches = _duplicates(match_ids)
    if duplicated_matches:
        errors.append("duplicate bracket round_of_32 ids: " + ", ".join(duplicated_matches))
    if len(raw_round_of_32) != tournament_format.opening_knockout_matches:
        errors.append(
            "bracket.round_of_32 must contain "
            f"{tournament_format.opening_knockout_matches} matches; "
            f"found {len(raw_round_of_32)}"
        )

    duplicated_group_positions = _duplicates(
        f"{group_id}{position}" for group_id, position in group_position_slots
    )
    if duplicated_group_positions:
        errors.append(
            "duplicate group-position bracket slots: "
            + ", ".join(duplicated_group_positions)
        )
    expected_group_positions = {
        (group_id, position) for group_id in group_ids for position in (1, 2)
    }
    missing_group_positions = sorted(
        expected_group_positions - set(group_position_slots)
    )
    if missing_group_positions:
        readable = ", ".join(
            f"{group_id}{position}" for group_id, position in missing_group_positions
        )
        errors.append(f"bracket.round_of_32 missing group-position slots: {readable}")

    duplicated_slots = _duplicates(third_place_slots)
    if duplicated_slots:
        errors.append("duplicate third-place bracket slots: " + ", ".join(duplicated_slots))
    if len(third_place_slots) != tournament_format.third_place_qualifiers:
        errors.append(
            "bracket.round_of_32 must contain "
            f"{tournament_format.third_place_qualifiers} third-place slots; "
            f"found {len(third_place_slots)}"
        )

    return third_place_slots, len(raw_round_of_32)


def _validate_bracket_source(
    source: Mapping[str, object],
    path: str,
    valid_group_ids: set[str],
    errors: list[str],
) -> tuple[list[tuple[str, int]], list[str]]:
    source_type = _required_string(source, "type", errors, parent=path)
    if source_type == "group_position":
        group_id = _required_string(source, "group_id", errors, parent=path)
        position = source.get("position")
        if group_id and group_id not in valid_group_ids:
            errors.append(f"{path}.group_id references unknown group: {group_id}")
        if position not in (1, 2):
            errors.append(f"{path}.position must be 1 or 2")
            return [], []
        return ([(group_id, position)] if group_id else []), []
    if source_type == "third_place_slot":
        slot_id = _required_string(source, "slot_id", errors, parent=path)
        return [], ([slot_id] if slot_id else [])

    errors.append(f"{path}.type must be 'group_position' or 'third_place_slot'")
    return [], []


def _validate_third_place_allocation(
    allocation: Mapping[str, object],
    group_ids: Sequence[str],
    opening_slots: Sequence[str],
    tournament_format: TournamentFormat,
    errors: list[str],
) -> tuple[str, int]:
    source_version = _required_string(
        allocation,
        "source_version",
        errors,
        parent="third_place_allocation",
    )
    _required_string(allocation, "source", errors, parent="third_place_allocation")
    raw_rules = _required_list(allocation, "rules", errors, parent="third_place_allocation")
    valid_group_ids = set(group_ids)
    expected_rule_count = comb(tournament_format.group_count, tournament_format.third_place_qualifiers)
    seen_combinations: set[tuple[str, ...]] = set()
    expected_slots = set(opening_slots)

    for index, raw_rule in enumerate(raw_rules):
        path = f"third_place_allocation.rules[{index}]"
        if not isinstance(raw_rule, Mapping):
            errors.append(f"{path} must be an object")
            continue
        raw_qualifying_groups = _required_list(raw_rule, "qualifying_groups", errors, parent=path)
        qualifying_groups = [
            str(group_id) for group_id in raw_qualifying_groups if isinstance(group_id, str)
        ]
        assignments = _required_mapping(raw_rule, "slot_assignments", errors, parent=path)
        combination = tuple(sorted(qualifying_groups))

        if len(qualifying_groups) != len(raw_qualifying_groups):
            errors.append(f"{path}.qualifying_groups must only contain strings")
        if len(qualifying_groups) != tournament_format.third_place_qualifiers:
            errors.append(
                f"{path}.qualifying_groups must contain "
                f"{tournament_format.third_place_qualifiers} groups; "
                f"found {len(qualifying_groups)}"
            )
        unknown_groups = sorted(set(qualifying_groups) - valid_group_ids)
        if unknown_groups:
            errors.append(
                f"{path}.qualifying_groups references unknown groups: "
                + ", ".join(unknown_groups)
            )
        duplicated_groups = _duplicates(qualifying_groups)
        if duplicated_groups:
            errors.append(
                f"{path}.qualifying_groups contains duplicates: "
                + ", ".join(duplicated_groups)
            )
        if combination in seen_combinations:
            errors.append(
                f"{path}.qualifying_groups duplicates allocation for "
                + ", ".join(combination)
            )
        seen_combinations.add(combination)

        assigned_slots = set(assignments)
        missing_slots = sorted(expected_slots - assigned_slots)
        extra_slots = sorted(assigned_slots - expected_slots)
        if missing_slots:
            errors.append(f"{path}.slot_assignments missing slots: {', '.join(missing_slots)}")
        if extra_slots:
            errors.append(f"{path}.slot_assignments references unknown slots: {', '.join(extra_slots)}")

        assigned_groups = [value for value in assignments.values() if isinstance(value, str)]
        if len(assigned_groups) != len(assignments):
            errors.append(f"{path}.slot_assignments values must be strings")
        unknown_assigned_groups = sorted(set(assigned_groups) - set(qualifying_groups))
        if unknown_assigned_groups:
            errors.append(
                f"{path}.slot_assignments uses non-qualifying groups: "
                + ", ".join(unknown_assigned_groups)
            )
        duplicated_assigned_groups = _duplicates(assigned_groups)
        if duplicated_assigned_groups:
            errors.append(
                f"{path}.slot_assignments repeats group(s): "
                + ", ".join(duplicated_assigned_groups)
            )

    if len(raw_rules) != expected_rule_count:
        errors.append(
            "third_place_allocation.rules must contain "
            f"{expected_rule_count} rules for this tournament format; "
            f"found {len(raw_rules)}"
        )

    expected_combinations = {
        tuple(sorted(group_set))
        for group_set in combinations(group_ids, tournament_format.third_place_qualifiers)
    }
    missing_combinations = sorted(expected_combinations - seen_combinations)
    if missing_combinations:
        preview = "; ".join(", ".join(group_set) for group_set in missing_combinations[:5])
        suffix = " ..." if len(missing_combinations) > 5 else ""
        errors.append(
            "third_place_allocation.rules missing qualifying group sets: "
            f"{preview}{suffix}"
        )

    return source_version, len(raw_rules)


def _required_string(
    mapping: Mapping[str, object],
    key: str,
    errors: list[str],
    *,
    parent: str | None = None,
) -> str:
    path = f"{parent}.{key}" if parent else key
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} is required")
        return ""
    return value.strip()


def _required_mapping(
    mapping: Mapping[str, object],
    key: str,
    errors: list[str],
    *,
    parent: str | None = None,
) -> Mapping[str, object]:
    path = f"{parent}.{key}" if parent else key
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        errors.append(f"{path} must be an object")
        return {}
    return value


def _required_list(
    mapping: Mapping[str, object],
    key: str,
    errors: list[str],
    *,
    parent: str | None = None,
) -> list[object]:
    path = f"{parent}.{key}" if parent else key
    value = mapping.get(key)
    if not isinstance(value, list):
        errors.append(f"{path} must be a list")
        return []
    return value


def _positive_int(
    mapping: Mapping[str, object],
    key: str,
    errors: list[str],
    fallback: int,
) -> int:
    value = mapping.get(key, fallback)
    if not isinstance(value, int) or value <= 0:
        errors.append(f"format.{key} must be a positive integer")
        return fallback
    return value


def _validate_utc_datetime(value: str, path: str, errors: list[str]) -> None:
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        errors.append(f"{path} must be an ISO-8601 UTC timestamp")
        return

    if parsed.utcoffset() is None:
        errors.append(f"{path} must include a UTC offset")
    elif parsed.utcoffset().total_seconds() != 0:
        errors.append(f"{path} must be in UTC")


def _duplicates(values: Sequence[str] | Any) -> list[str]:
    counter = Counter(values)
    return sorted(value for value, count in counter.items() if count > 1)
