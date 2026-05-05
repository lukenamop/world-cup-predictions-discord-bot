from __future__ import annotations

import unittest

from world_cup_bot.domain.predictions import (
    ROUND_ORDER,
    TournamentModel,
    empty_prediction_data,
    get_round_matches,
    next_prediction_step,
    record_group_pick,
    record_knockout_winner,
    record_third_place_qualifiers,
)
from world_cup_bot.domain.scoring import ScoringRules, score_prediction
from world_cup_bot.domain.standings import (
    MatchResult,
    best_third_place_qualifiers,
    compute_group_standings,
)
from world_cup_bot.services.live_results_client import LiveMatchResult
from world_cup_bot.services.result_sync_service import (
    ResultSyncService,
    ResultSyncServiceError,
    _map_live_results,
)


class StandingsTests(unittest.TestCase):
    def test_group_standings_and_best_thirds_use_table_order(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        results = _group_results_for_rankings(model)

        standings = compute_group_standings(model, results)

        self.assertEqual(
            [row.team_id for row in standings["A"]],
            ["A1", "A2", "A3"],
        )
        self.assertEqual(
            best_third_place_qualifiers(model, standings),
            ("A3", "B3", "C3", "D3", "E3", "F3", "G3", "H3"),
        )


class ScoringTests(unittest.TestCase):
    def test_score_prediction_counts_groups_knockout_and_placements(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        prediction = _complete_home_winner_prediction(model)
        results = _group_results_for_rankings(model) + _knockout_results_from_prediction(
            model,
            prediction,
        )

        score = score_prediction(model, prediction, results, rules=ScoringRules())

        self.assertEqual(score.group_points, 68)
        self.assertEqual(score.knockout_points, 224)
        self.assertEqual(score.total_points, 292)

    def test_group_points_wait_for_completed_groups(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        prediction = _complete_home_winner_prediction(model)
        partial_results = [
            MatchResult(
                match_id="A-1",
                stage="group",
                group_id="A",
                home_team_id="A1",
                away_team_id="A2",
                status="FINISHED",
                home_score=2,
                away_score=0,
            )
        ]

        score = score_prediction(model, prediction, partial_results)

        self.assertEqual(score.group_points, 0)

    def test_knockout_advancement_counts_teams_reaching_round(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        prediction = _complete_home_winner_prediction(model)
        first_round_match = get_round_matches(model, prediction, "round_of_32")[0]
        results = _group_results_for_rankings(model) + [
            MatchResult(
                match_id=first_round_match.id,
                stage="knockout",
                round_name="round_of_32",
                home_team_id=first_round_match.home_team_id,
                away_team_id=first_round_match.away_team_id,
                status="FINISHED",
                home_score=0,
                away_score=1,
                winner_team_id=first_round_match.away_team_id,
            )
        ]

        score = score_prediction(model, prediction, results)
        round_of_32 = score.details["knockout"]["advancement"][0]

        self.assertIn(first_round_match.home_team_id, round_of_32["hits"])
        self.assertEqual(round_of_32["points"], 32)

    def test_later_knockout_points_wait_for_finished_matches(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        prediction = _complete_home_winner_prediction(model)
        first_round_match = get_round_matches(model, prediction, "round_of_32")[0]
        results = _group_results_for_rankings(model) + [
            MatchResult(
                match_id=first_round_match.id,
                stage="knockout",
                round_name="round_of_32",
                home_team_id=first_round_match.home_team_id,
                away_team_id=first_round_match.away_team_id,
                status="IN_PLAY",
                home_score=1,
                away_score=0,
                winner_team_id=first_round_match.home_team_id,
            )
        ]

        score = score_prediction(model, prediction, results)
        advancement = {
            row["round"]: row["points"]
            for row in score.details["knockout"]["advancement"]
        }

        self.assertEqual(advancement["round_of_32"], 32)
        self.assertEqual(advancement["round_of_16"], 0)
        self.assertEqual(advancement["quarter_finals"], 0)
        self.assertEqual(advancement["semi_finals"], 0)
        self.assertEqual(advancement["final"], 0)


class ResultSyncMappingTests(unittest.TestCase):
    def test_live_results_map_by_provider_match_id(self) -> None:
        config = {
            "fixtures": [
                {
                    "id": "fixture-1",
                    "provider_match_id": "1001",
                    "stage": "group",
                    "group_id": "A",
                    "home_team_id": "A1",
                    "away_team_id": "A2",
                    "kickoff_utc": "2026-06-11T18:00:00Z",
                }
            ]
        }
        live = [
            LiveMatchResult(
                provider_match_id="1001",
                status="FINISHED",
                home_score=2,
                away_score=1,
                played_at=None,
                payload={"id": 1001},
            )
        ]

        stored, skipped = _map_live_results(
            provider_name="football_data_org",
            tournament_config=config,
            live_results=live,
        )

        self.assertEqual(skipped, [])
        self.assertEqual(stored[0].match_id, "fixture-1")
        self.assertEqual(stored[0].winner_team_id, "A1")

    def test_live_results_map_knockout_matches_from_configured_provider_ids(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        config = _prediction_config()
        group_results = _group_results_for_rankings(model)
        config["fixtures"] = [
            {
                "id": result.match_id,
                "provider_match_id": result.match_id,
                "stage": "group",
                "group_id": result.group_id,
                "home_team_id": result.home_team_id,
                "away_team_id": result.away_team_id,
                "kickoff_utc": "2026-06-11T18:00:00Z",
            }
            for result in group_results
        ]
        config["knockout_fixtures"] = [
            {
                "id": "R32-1",
                "stage": "knockout",
                "round_name": "round_of_32",
                "provider_match_id": "ko-1",
                "kickoff_utc": "2026-06-28T18:00:00Z",
            }
        ]
        live_results = [
            LiveMatchResult(
                provider_match_id=result.match_id,
                status="FINISHED",
                home_score=result.home_score,
                away_score=result.away_score,
                played_at=None,
            )
            for result in group_results
        ]
        live_results.append(
            LiveMatchResult(
                provider_match_id="ko-1",
                status="FINISHED",
                home_score=1,
                away_score=1,
                played_at=None,
                winner_side="AWAY_TEAM",
            )
        )

        stored, skipped = _map_live_results(
            provider_name="football_data_org",
            tournament_config=config,
            live_results=live_results,
        )

        knockout_result = next(result for result in stored if result.match_id == "R32-1")
        self.assertEqual(skipped, [])
        self.assertEqual(knockout_result.stage, "knockout")
        self.assertEqual(knockout_result.round_name, "round_of_32")
        self.assertEqual(knockout_result.home_team_id, "A1")
        self.assertEqual(knockout_result.away_team_id, "A2")
        self.assertEqual(knockout_result.winner_team_id, "A2")

    def test_in_play_knockout_leader_does_not_seed_later_rounds(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        config = _prediction_config()
        group_results = _group_results_for_rankings(model)
        config["fixtures"] = [
            {
                "id": result.match_id,
                "provider_match_id": result.match_id,
                "stage": "group",
                "group_id": result.group_id,
                "home_team_id": result.home_team_id,
                "away_team_id": result.away_team_id,
                "kickoff_utc": "2026-06-11T18:00:00Z",
            }
            for result in group_results
        ]
        config["knockout_fixtures"] = [
            {
                "id": f"R32-{index}",
                "stage": "knockout",
                "round_name": "round_of_32",
                "provider_match_id": f"ko-r32-{index}",
            }
            for index in range(1, 17)
        ]
        config["knockout_fixtures"].append(
            {
                "id": "R16-1",
                "stage": "knockout",
                "round_name": "round_of_16",
                "provider_match_id": "ko-r16-1",
            }
        )
        live_results = [
            LiveMatchResult(
                provider_match_id=result.match_id,
                status="FINISHED",
                home_score=result.home_score,
                away_score=result.away_score,
                played_at=None,
            )
            for result in group_results
        ]
        live_results.extend(
            LiveMatchResult(
                provider_match_id=f"ko-r32-{index}",
                status="IN_PLAY" if index == 16 else "FINISHED",
                home_score=1,
                away_score=0,
                played_at=None,
            )
            for index in range(1, 17)
        )
        live_results.append(
            LiveMatchResult(
                provider_match_id="ko-r16-1",
                status="FINISHED",
                home_score=1,
                away_score=0,
                played_at=None,
            )
        )

        stored, skipped = _map_live_results(
            provider_name="football_data_org",
            tournament_config=config,
            live_results=live_results,
        )

        in_play_result = next(result for result in stored if result.match_id == "R32-16")
        self.assertIsNone(in_play_result.winner_team_id)
        self.assertNotIn("R16-1", {result.match_id for result in stored})
        self.assertIn("ko-r16-1", skipped)


class ResultSyncServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_provider_raises_service_error_before_sync_run(self) -> None:
        service = ResultSyncService(
            object(),
            provider_name="unsupported_provider",
            api_key=None,
        )
        service.tournaments = _ActiveTournamentRepo()
        service.results = _UnexpectedResultRepo()

        with self.assertRaisesRegex(
            ResultSyncServiceError,
            "Unsupported live results provider",
        ):
            await service.sync_guild(guild_id="guild-1")


class _ActiveTournamentRepo:
    async def get_active_config(self, guild_id: str) -> object:
        return _ActiveTournament()


class _ActiveTournament:
    id = 1
    config: dict[str, object] = {"tournament": {"id": "test", "name": "Test Cup"}}


class _UnexpectedResultRepo:
    async def start_sync_run(self, **kwargs: object) -> int:
        raise AssertionError("unsupported providers should not start sync runs")


def _complete_home_winner_prediction(model: TournamentModel) -> dict[str, object]:
    data = empty_prediction_data()
    while True:
        step = next_prediction_step(model, data)
        if step.kind == "submit":
            return data
        if step.kind == "group_pick":
            data = record_group_pick(
                model,
                data,
                group_id=step.group_id or "",
                team_id=step.options[0].id,
            )
        elif step.kind == "third_place":
            data = record_third_place_qualifiers(
                model,
                data,
                team_ids=[team.id for team in step.options[: step.max_values]],
            )
        elif step.kind == "knockout":
            data = record_knockout_winner(
                model,
                data,
                round_name=step.round_name or "",
                match_id=step.match_id or "",
                winner_team_id=step.options[0].id,
            )


def _group_results_for_rankings(model: TournamentModel) -> list[MatchResult]:
    results: list[MatchResult] = []
    for group in model.groups:
        first, second, third = group.team_ids
        results.extend(
            [
                MatchResult(
                    match_id=f"{group.id}-1",
                    stage="group",
                    group_id=group.id,
                    home_team_id=first,
                    away_team_id=second,
                    status="FINISHED",
                    home_score=2,
                    away_score=0,
                ),
                MatchResult(
                    match_id=f"{group.id}-2",
                    stage="group",
                    group_id=group.id,
                    home_team_id=first,
                    away_team_id=third,
                    status="FINISHED",
                    home_score=2,
                    away_score=0,
                ),
                MatchResult(
                    match_id=f"{group.id}-3",
                    stage="group",
                    group_id=group.id,
                    home_team_id=second,
                    away_team_id=third,
                    status="FINISHED",
                    home_score=2,
                    away_score=0,
                ),
            ]
        )
    return results


def _knockout_results_from_prediction(
    model: TournamentModel,
    prediction: dict[str, object],
) -> list[MatchResult]:
    results: list[MatchResult] = []
    for round_name in ROUND_ORDER:
        for match in get_round_matches(model, prediction, round_name):
            results.append(
                MatchResult(
                    match_id=match.id,
                    stage="knockout",
                    round_name=round_name,
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                    status="FINISHED",
                    home_score=1,
                    away_score=0,
                    winner_team_id=match.winner_team_id,
                )
            )
    return results


def _prediction_config() -> dict[str, object]:
    group_ids = list("ABCDEFGHIJKL")
    teams = [
        {"id": f"{group_id}{position}", "name": f"Team {group_id}{position}"}
        for group_id in group_ids
        for position in range(1, 4)
    ]
    groups = [
        {
            "id": group_id,
            "label": f"Group {group_id}",
            "team_ids": [f"{group_id}{position}" for position in range(1, 4)],
        }
        for group_id in group_ids
    ]
    sources = [
        {"type": "group_position", "group_id": group_id, "position": position}
        for group_id in group_ids
        for position in (1, 2)
    ]
    sources.extend(
        {"type": "third_place_slot", "slot_id": f"TP-{index}"}
        for index in range(1, 9)
    )
    round_of_32 = [
        {
            "id": f"R32-{index + 1}",
            "home_source": sources[index * 2],
            "away_source": sources[index * 2 + 1],
        }
        for index in range(16)
    ]
    return {
        "schema_version": "test",
        "tournament": {"id": "test", "name": "Test Cup"},
        "format": {
            "group_count": 12,
            "teams_per_group": 3,
            "third_place_qualifiers": 8,
            "opening_knockout_matches": 16,
        },
        "teams": teams,
        "groups": groups,
        "fixtures": [],
        "bracket": {"round_of_32": round_of_32},
        "third_place_allocation": {
            "rules": [
                {
                    "qualifying_groups": group_ids[:8],
                    "slot_assignments": {
                        f"TP-{index}": group_id
                        for index, group_id in enumerate(group_ids[:8], start=1)
                    },
                }
            ]
        },
    }
