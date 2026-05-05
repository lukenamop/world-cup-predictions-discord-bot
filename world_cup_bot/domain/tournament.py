from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TournamentFormat:
    group_count: int
    teams_per_group: int
    third_place_qualifiers: int
    opening_knockout_matches: int


@dataclass(frozen=True)
class TournamentSummary:
    tournament_id: str
    name: str
    schema_version: str
    team_count: int
    group_count: int
    fixture_count: int
    opening_knockout_matches: int
    third_place_rule_count: int
    source_version: str


@dataclass(frozen=True)
class TournamentValidationReport:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: TournamentSummary | None


DEFAULT_2026_FORMAT = TournamentFormat(
    group_count=12,
    teams_per_group=4,
    third_place_qualifiers=8,
    opening_knockout_matches=16,
)


def tournament_identity(config: Mapping[str, Any]) -> tuple[str, str, str]:
    tournament = config.get("tournament")
    if not isinstance(tournament, Mapping):
        return "", "", ""

    return (
        str(tournament.get("id") or ""),
        str(tournament.get("name") or ""),
        str(config.get("schema_version") or ""),
    )
