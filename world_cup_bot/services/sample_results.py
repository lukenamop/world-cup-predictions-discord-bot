from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from world_cup_bot.services.live_results_client import LiveMatchResult


SAMPLE_RESULTS_PROVIDER = "sample_live_sync"
SAMPLE_RESULTS_ROUNDS = ("round_of_32", "round_of_16")


def build_sample_results_through_round_of_16(
    tournament_config: Mapping[str, Any],
) -> list[LiveMatchResult]:
    """Build deterministic provider-style results through the Round of 16."""
    return [
        *_sample_group_results(tournament_config),
        *_sample_knockout_results(tournament_config),
    ]


def _sample_group_results(
    tournament_config: Mapping[str, Any],
) -> list[LiveMatchResult]:
    groups = {
        str(group["id"]): [str(team_id) for team_id in group["team_ids"]]
        for group in _mappings(tournament_config.get("groups"))
    }
    group_order = [str(group["id"]) for group in _mappings(tournament_config.get("groups"))]
    strong_third_place_groups = set(group_order[:8])
    live_results: list[LiveMatchResult] = []
    for fixture in _mappings(tournament_config.get("fixtures")):
        group_id = str(fixture.get("group_id") or "")
        ranking = groups.get(group_id)
        if ranking is None:
            continue
        provider_match_id = _provider_match_id(fixture)
        if provider_match_id is None:
            continue
        home_team_id = str(fixture["home_team_id"])
        away_team_id = str(fixture["away_team_id"])
        home_position = ranking.index(home_team_id)
        away_position = ranking.index(away_team_id)
        home_score, away_score, winner_side = _group_fixture_score(
            home_position=home_position,
            away_position=away_position,
            strong_third_place=group_id in strong_third_place_groups,
        )
        live_results.append(
            _live_result(
                provider_match_id=provider_match_id,
                fixture=fixture,
                home_score=home_score,
                away_score=away_score,
                winner_side=winner_side,
                stage="group",
            )
        )
    return live_results


def _sample_knockout_results(
    tournament_config: Mapping[str, Any],
) -> list[LiveMatchResult]:
    live_results: list[LiveMatchResult] = []
    for fixture in _mappings(tournament_config.get("knockout_fixtures")):
        round_name = fixture.get("round_name")
        if round_name not in SAMPLE_RESULTS_ROUNDS:
            continue
        provider_match_id = _provider_match_id(fixture)
        if provider_match_id is None:
            continue
        live_results.append(
            _live_result(
                provider_match_id=provider_match_id,
                fixture=fixture,
                home_score=2,
                away_score=1,
                winner_side="HOME_TEAM",
                stage=str(round_name),
            )
        )
    return live_results


def _group_fixture_score(
    *,
    home_position: int,
    away_position: int,
    strong_third_place: bool,
) -> tuple[int, int, str | None]:
    pair = {home_position, away_position}
    if not strong_third_place and pair == {1, 2}:
        return 1, 1, None
    if not strong_third_place and pair == {2, 3}:
        return 0, 0, None

    better_position = min(home_position, away_position)
    worse_position = max(home_position, away_position)
    margin = 1 if {better_position, worse_position} == {0, 1} else 2
    winner_side = "HOME_TEAM" if home_position < away_position else "AWAY_TEAM"
    if winner_side == "HOME_TEAM":
        return margin, 0, winner_side
    return 0, margin, winner_side


def _live_result(
    *,
    provider_match_id: str,
    fixture: Mapping[str, Any],
    home_score: int,
    away_score: int,
    winner_side: str | None,
    stage: str,
) -> LiveMatchResult:
    played_at = _fixture_kickoff(fixture)
    return LiveMatchResult(
        provider_match_id=provider_match_id,
        status="FINISHED",
        home_score=home_score,
        away_score=away_score,
        played_at=played_at,
        winner_side=winner_side,
        payload={
            "IdMatch": provider_match_id,
            "MatchStatus": 0,
            "HomeTeamScore": home_score,
            "AwayTeamScore": away_score,
            "WinnerSide": winner_side,
            "SampleResult": True,
            "SampleStage": stage,
        },
    )


def _provider_match_id(fixture: Mapping[str, Any]) -> str | None:
    value = fixture.get("provider_match_id") or fixture.get("id")
    return value if isinstance(value, str) and value else None


def _fixture_kickoff(fixture: Mapping[str, Any]) -> datetime | None:
    kickoff = fixture.get("kickoff_utc")
    if not isinstance(kickoff, str):
        return None
    normalized = kickoff.removesuffix("Z") + "+00:00" if kickoff.endswith("Z") else kickoff
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _mappings(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]
