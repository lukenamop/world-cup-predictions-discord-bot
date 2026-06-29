from __future__ import annotations

import json
import random
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from world_cup_bot.data.repositories import ResultRepository, StoredMatchResult
from world_cup_bot.domain.predictions import (
    ROUND_ORDER,
    RoundMatch,
    TournamentModel,
    empty_prediction_data,
    get_round_matches,
    is_submission_complete,
    next_prediction_step,
    record_group_pick,
    record_knockout_winner,
    record_third_place_qualifiers,
)
from world_cup_bot.domain.scoring import ScoringRules, score_prediction
from world_cup_bot.domain.standings import (
    MatchResult,
    StandingAdjudication,
    StandingResolutionError,
    TeamStanding,
    best_third_place_qualifiers,
    compute_group_standings,
)
from world_cup_bot.services.live_results_client import (
    FifaPublicCalendarClient,
    LiveMatchResult,
    _parse_fifa_match,
)
from world_cup_bot.services.result_sync_service import (
    ResultSyncService,
    ResultSyncServiceError,
    _map_live_results,
)
from world_cup_bot.services.sample_results import build_sample_results_through_round_of_16
from world_cup_bot.services.sample_predictions import build_random_prediction_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MAIN_BRACKET_ROUNDS = tuple(
    round_name for round_name in ROUND_ORDER if round_name != "third_place"
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

    def test_group_standings_use_head_to_head_before_goal_difference(self) -> None:
        model = TournamentModel.from_config(_four_team_group_config())
        results = [
            _group_result("A-1", "A", "A1", "A2", 1, 0),
            _group_result("A-2", "A", "A1", "A3", 1, 0),
            _group_result("A-3", "A", "A1", "A4", 0, 3),
            _group_result("A-4", "A", "A2", "A3", 5, 0),
            _group_result("A-5", "A", "A2", "A4", 5, 0),
            _group_result("A-6", "A", "A3", "A4", 1, 0),
        ]

        standings = compute_group_standings(model, results)

        self.assertEqual(
            [row.team_id for row in standings["A"]],
            ["A1", "A2", "A3", "A4"],
        )

    def test_group_standings_raise_for_unresolved_official_tie(self) -> None:
        model = TournamentModel.from_config(_simple_group_config())
        results = [
            _group_result("A-1", "A", "A1", "A2", 0, 0),
            _group_result("A-2", "A", "A1", "A3", 0, 0),
            _group_result("A-3", "A", "A2", "A3", 0, 0),
        ]

        with self.assertRaises(StandingResolutionError) as raised:
            compute_group_standings(model, results)

        self.assertEqual(raised.exception.unresolved_ties[0].scope, "group")
        self.assertEqual(raised.exception.unresolved_ties[0].group_id, "A")

    def test_group_standings_use_operator_adjudication_for_unresolved_tie(self) -> None:
        model = TournamentModel.from_config(_simple_group_config())
        results = [
            _group_result("A-1", "A", "A1", "A2", 0, 0),
            _group_result("A-2", "A", "A1", "A3", 0, 0),
            _group_result("A-3", "A", "A2", "A3", 0, 0),
        ]

        standings = compute_group_standings(
            model,
            results,
            adjudications=[
                StandingAdjudication(
                    scope="group",
                    group_id="A",
                    ordered_team_ids=("A2", "A3", "A1"),
                    reason="Official FIFA ranking fallback",
                )
            ],
        )

        self.assertEqual(
            [row.team_id for row in standings["A"]],
            ["A2", "A3", "A1"],
        )

    def test_best_thirds_raise_for_unresolved_tie(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        rows = {
            group.id: (
                _standing(group.id, group.team_ids[0], 6, 4, 3),
                _standing(group.id, group.team_ids[1], 3, 0, 3),
                _standing(group.id, group.team_ids[2], 0, -4, 0),
            )
            for group in model.groups
        }

        with self.assertRaises(StandingResolutionError) as raised:
            best_third_place_qualifiers(model, rows)

        self.assertEqual(raised.exception.unresolved_ties[0].scope, "best_third")

    def test_best_thirds_use_operator_adjudication_for_unresolved_tie(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        rows = {
            group.id: (
                _standing(group.id, group.team_ids[0], 6, 4, 3),
                _standing(group.id, group.team_ids[1], 3, 0, 3),
                _standing(group.id, group.team_ids[2], 0, -4, 0),
            )
            for group in model.groups
        }
        ordered = tuple(f"{group_id}3" for group_id in "LKJIHGFEDCBA")

        qualifiers = best_third_place_qualifiers(
            model,
            rows,
            adjudications=[
                StandingAdjudication(
                    scope="best_third",
                    ordered_team_ids=ordered,
                    reason="Official FIFA ranking fallback",
                )
            ],
        )

        self.assertEqual(qualifiers, ordered[:8])

    def test_best_thirds_ignore_ties_below_qualifier_cutoff(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        rows = {}
        for group_index, group in enumerate(model.groups):
            third_points = 3 if group_index < 8 else 0
            rows[group.id] = (
                _standing(group.id, group.team_ids[0], 6, 4, 3),
                _standing(group.id, group.team_ids[1], 3, 0, 3),
                _standing(group.id, group.team_ids[2], third_points, 0, 1),
            )

        qualifiers = best_third_place_qualifiers(model, rows)

        self.assertEqual(
            qualifiers,
            ("A3", "B3", "C3", "D3", "E3", "F3", "G3", "H3"),
        )

    def test_best_thirds_return_all_rows_when_all_available_qualify(self) -> None:
        model = TournamentModel.from_config(_simple_group_config())
        rows = {
            "A": (
                _standing("A", "A1", 6, 2, 2),
                _standing("A", "A2", 3, 0, 1),
                _standing("A", "A3", 0, -2, 0),
            )
        }

        qualifiers = best_third_place_qualifiers(model, rows)

        self.assertEqual(qualifiers, ("A3",))


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

    def test_knockout_advancement_counts_partial_winners_for_each_later_round(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        prediction = _complete_home_winner_prediction(model)
        transitions = (
            ("round_of_32", "round_of_16", 2),
            ("round_of_16", "quarter_finals", 5),
            ("quarter_finals", "semi_finals", 10),
            ("semi_finals", "final", 15),
        )

        for current_round, next_round, expected_points in transitions:
            with self.subTest(current_round=current_round, next_round=next_round):
                results = _group_results_for_rankings(model)
                for round_name in _MAIN_BRACKET_ROUNDS:
                    if round_name == current_round:
                        break
                    results.extend(
                        _knockout_results_for_round(model, prediction, round_name)
                    )
                current_match = get_round_matches(model, prediction, current_round)[0]
                results.append(_knockout_result_for_match(current_round, current_match))

                score = score_prediction(model, prediction, results)
                advancement = {
                    row["round"]: row
                    for row in score.details["knockout"]["advancement"]
                }

                self.assertIn(current_match.winner_team_id, advancement[next_round]["hits"])
                self.assertEqual(advancement[next_round]["points"], expected_points)

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

    def test_generated_knockout_round_ids_are_contiguous(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        prediction = _complete_home_winner_prediction(model)

        self.assertEqual(
            [match.id for match in get_round_matches(model, prediction, "round_of_16")],
            [f"R16-{index}" for index in range(1, 9)],
        )

    def test_sample_prediction_builder_generates_complete_valid_prediction(self) -> None:
        model = TournamentModel.from_config(_prediction_config())

        prediction = build_random_prediction_data(model, randomizer=random.Random(7))

        self.assertTrue(is_submission_complete(model, prediction))
        self.assertEqual(len(prediction["group_rankings"]), len(model.groups))
        self.assertEqual(
            len(prediction["third_place_qualifier_team_ids"]),
            model.format.third_place_qualifiers,
        )
        self.assertEqual(
            len(prediction["knockout"]["round_of_32"]),
            model.format.opening_knockout_matches,
        )


class ResultSyncMappingTests(unittest.TestCase):
    def test_fifa_public_calendar_url_uses_config_metadata(self) -> None:
        url = FifaPublicCalendarClient(
            user_agent="WorldCupBot/1.0",
            base_url="https://example.test",
            match_count=104,
        )._matches_url(
            {
                "tournament": {
                    "start_date": "2026-06-11",
                    "end_date": "2026-07-19",
                    "source_metadata": {
                        "tournament_data": {"competition_id": "17"}
                    },
                }
            }
        )

        self.assertIn("https://example.test/calendar/matches?", url)
        self.assertIn("from=2026-06-11T00%3A00%3A00Z", url)
        self.assertIn("to=2026-07-19T23%3A59%3A59Z", url)
        self.assertIn("count=104", url)
        self.assertIn("idCompetition=17", url)

    def test_fifa_public_calendar_url_falls_back_to_fixture_dates(self) -> None:
        url = FifaPublicCalendarClient(
            user_agent="WorldCupBot/1.0",
            base_url="https://example.test",
            match_count=104,
        )._matches_url(
            {
                "tournament": {
                    "source_metadata": {
                        "tournament_data": {"competition_id": "17"}
                    },
                },
                "fixtures": [
                    {"kickoff_utc": "2026-06-12T02:00:00Z"},
                    {"kickoff_utc": "2026-06-11T19:00:00Z"},
                ],
                "knockout_fixtures": [
                    {"kickoff_utc": "2026-07-19T19:00:00Z"},
                ],
            }
        )

        self.assertIn("from=2026-06-11T00%3A00%3A00Z", url)
        self.assertIn("to=2026-07-19T23%3A59%3A59Z", url)

    def test_fifa_public_calendar_request_uses_configured_user_agent(self) -> None:
        captured_requests = []

        def fake_urlopen(request: object, *, timeout: int) -> object:
            captured_requests.append(request)
            self.assertEqual(timeout, 20)
            return _FakeFifaResponse()

        client = FifaPublicCalendarClient(
            user_agent="WorldCupBot/1.0 (contact: ops@example.com)",
            base_url="https://example.test",
        )

        with patch("world_cup_bot.services.live_results_client.urlopen", fake_urlopen):
            results = client._fetch_matches_sync("https://example.test/calendar/matches")

        self.assertEqual(results, [])
        headers = dict(captured_requests[0].header_items())
        self.assertEqual(
            headers["User-agent"],
            "WorldCupBot/1.0 (contact: ops@example.com)",
        )
        self.assertEqual(headers["Accept"], "application/json")

    def test_fifa_public_calendar_parser_maps_result_fields(self) -> None:
        live_result = _parse_fifa_match(
            {
                "IdMatch": "400128082",
                "Date": "2022-11-20T16:00:00Z",
                "HomeTeamScore": 0,
                "AwayTeamScore": 2,
                "Winner": "43927",
                "MatchStatus": 0,
                "Home": {"IdTeam": "43834", "Score": 0},
                "Away": {"IdTeam": "43927", "Score": 2},
            }
        )

        self.assertEqual(live_result.provider_match_id, "400128082")
        self.assertEqual(live_result.status, "FINISHED")
        self.assertEqual(live_result.home_score, 0)
        self.assertEqual(live_result.away_score, 2)
        self.assertEqual(live_result.winner_side, "AWAY_TEAM")

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
                payload={"id": 1001, "LastUpdated": "changes-every-fetch"},
            )
        ]

        stored, skipped = _map_live_results(
            provider_name="fifa_public_calendar",
            tournament_config=config,
            live_results=live,
        )

        self.assertEqual(skipped, [])
        self.assertEqual(stored[0].match_id, "fixture-1")
        self.assertEqual(stored[0].winner_team_id, "A1")
        self.assertEqual(
            stored[0].provider_payload,
            {
                "provider_match_id": "1001",
                "status": "FINISHED",
                "home_score": 2,
                "away_score": 1,
                "played_at": None,
                "winner_side": None,
            },
        )

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
            provider_name="fifa_public_calendar",
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
            provider_name="fifa_public_calendar",
            tournament_config=config,
            live_results=live_results,
        )

        in_play_result = next(result for result in stored if result.match_id == "R32-16")
        self.assertIsNone(in_play_result.winner_team_id)
        self.assertNotIn("R16-1", {result.match_id for result in stored})
        self.assertIn("ko-r16-1", skipped)

    def test_unresolved_group_tie_keeps_group_results_and_skips_knockout(self) -> None:
        config = _simple_group_config()
        group_results = [
            _group_result("A-1", "A", "A1", "A2", 0, 0),
            _group_result("A-2", "A", "A1", "A3", 0, 0),
            _group_result("A-3", "A", "A2", "A3", 0, 0),
        ]
        config["fixtures"] = [
            {
                "id": result.match_id,
                "provider_match_id": result.match_id,
                "stage": "group",
                "group_id": result.group_id,
                "home_team_id": result.home_team_id,
                "away_team_id": result.away_team_id,
            }
            for result in group_results
        ]
        config["knockout_fixtures"] = [
            {
                "id": "R32-1",
                "stage": "knockout",
                "round_name": "round_of_32",
                "provider_match_id": "ko-1",
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
                away_score=0,
                played_at=None,
            )
        )

        stored, skipped = _map_live_results(
            provider_name="fifa_public_calendar",
            tournament_config=config,
            live_results=live_results,
        )

        self.assertEqual(
            {result.match_id for result in stored},
            {"A-1", "A-2", "A-3"},
        )
        self.assertEqual(skipped, ["ko-1"])

    def test_sample_results_map_complete_through_round_of_16(self) -> None:
        config = _canonical_tournament_config()
        live_results = build_sample_results_through_round_of_16(config)

        stored, skipped = _map_live_results(
            provider_name="sample_live_sync",
            tournament_config=config,
            live_results=live_results,
        )

        self.assertEqual(skipped, [])
        self.assertEqual(len(live_results), 96)
        self.assertEqual(
            len([result for result in stored if result.stage == "group"]),
            72,
        )
        self.assertEqual(
            len([result for result in stored if result.round_name == "round_of_32"]),
            16,
        )
        self.assertEqual(
            len([result for result in stored if result.round_name == "round_of_16"]),
            8,
        )


class ResultSyncServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_provider_raises_service_error_before_sync_run(self) -> None:
        service = ResultSyncService(
            object(),
            provider_name="unsupported_provider",
        )
        service.tournaments = _ActiveTournamentRepo()
        service.results = _UnexpectedResultRepo()

        with self.assertRaisesRegex(
            ResultSyncServiceError,
            "Unsupported live results provider",
        ):
            await service.sync_guild(guild_id="guild-1")


class ResultRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_match_results_counts_only_inserted_or_changed_rows(self) -> None:
        pool = _FakePool(fetchval_results=[101, None])
        repository = ResultRepository(pool)

        applied = await repository.upsert_match_results(
            guild_id="guild-1",
            tournament_config_id=7,
            results=[
                _stored_match_result("A-1"),
                _stored_match_result("A-2"),
            ],
        )

        self.assertEqual(applied, 1)
        self.assertEqual(pool.connection.fetchval_call_count, 2)
        self.assertIn("is distinct from", pool.connection.fetchval_sql.lower())
        self.assertIn("returning id", pool.connection.fetchval_sql.lower())


class _ActiveTournamentRepo:
    async def get_active_config(self, guild_id: str) -> object:
        return _ActiveTournament()


class _ActiveTournament:
    id = 1
    config: dict[str, object] = {"tournament": {"id": "test", "name": "Test Cup"}}


class _UnexpectedResultRepo:
    async def start_sync_run(self, **kwargs: object) -> int:
        raise AssertionError("unsupported providers should not start sync runs")


class _FakePool:
    def __init__(self, *, fetchval_results: list[int | None]) -> None:
        self.connection = _FakeConnection(fetchval_results)

    def acquire(self) -> "_FakeAcquire":
        return _FakeAcquire(self.connection)


class _FakeAcquire:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection

    async def __aenter__(self) -> "_FakeConnection":
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeConnection:
    def __init__(self, fetchval_results: list[int | None]) -> None:
        self.fetchval_results = fetchval_results
        self.fetchval_call_count = 0
        self.fetchval_sql = ""

    def transaction(self) -> "_FakeTransaction":
        return _FakeTransaction()

    async def fetchval(self, sql: str, *args: object) -> int | None:
        self.fetchval_call_count += 1
        self.fetchval_sql = sql
        return self.fetchval_results.pop(0)


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeFifaResponse:
    def __enter__(self) -> "_FakeFifaResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"Results": []}'


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
    for group_index, group in enumerate(model.groups):
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
                    home_score=group_index + 1,
                    away_score=0,
                ),
                MatchResult(
                    match_id=f"{group.id}-3",
                    stage="group",
                    group_id=group.id,
                    home_team_id=second,
                    away_team_id=third,
                    status="FINISHED",
                    home_score=1,
                    away_score=0,
                ),
            ]
        )
    return results


def _group_result(
    match_id: str,
    group_id: str,
    home_team_id: str,
    away_team_id: str,
    home_score: int,
    away_score: int,
) -> MatchResult:
    return MatchResult(
        match_id=match_id,
        stage="group",
        group_id=group_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        status="FINISHED",
        home_score=home_score,
        away_score=away_score,
    )


def _standing(
    group_id: str,
    team_id: str,
    points: int,
    goal_difference: int,
    goals_for: int,
) -> TeamStanding:
    return TeamStanding(
        group_id=group_id,
        team_id=team_id,
        played=3,
        wins=0,
        draws=0,
        losses=0,
        goals_for=goals_for,
        goals_against=goals_for - goal_difference,
        points=points,
    )


def _stored_match_result(match_id: str) -> StoredMatchResult:
    return StoredMatchResult(
        match_id=match_id,
        provider="fifa_public_calendar",
        provider_match_id=f"provider-{match_id}",
        stage="group",
        round_name=None,
        group_id="A",
        home_team_id="A1",
        away_team_id="A2",
        home_score=1,
        away_score=0,
        status="FINISHED",
        winner_team_id="A1",
        played_at=datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
        provider_payload={"id": match_id},
    )


def _knockout_results_from_prediction(
    model: TournamentModel,
    prediction: dict[str, object],
) -> list[MatchResult]:
    results: list[MatchResult] = []
    for round_name in ROUND_ORDER:
        results.extend(_knockout_results_for_round(model, prediction, round_name))
    return results


def _knockout_results_for_round(
    model: TournamentModel,
    prediction: dict[str, object],
    round_name: str,
) -> list[MatchResult]:
    return [
        _knockout_result_for_match(round_name, match)
        for match in get_round_matches(model, prediction, round_name)
    ]


def _knockout_result_for_match(round_name: str, match: RoundMatch) -> MatchResult:
    return MatchResult(
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


def _simple_group_config() -> dict[str, object]:
    return {
        "schema_version": "test",
        "tournament": {"id": "simple", "name": "Simple Cup"},
        "format": {
            "group_count": 1,
            "teams_per_group": 3,
            "third_place_qualifiers": 1,
            "opening_knockout_matches": 1,
        },
        "teams": [
            {"id": "A1", "name": "Team A1"},
            {"id": "A2", "name": "Team A2"},
            {"id": "A3", "name": "Team A3"},
        ],
        "groups": [
            {
                "id": "A",
                "label": "Group A",
                "team_ids": ["A1", "A2", "A3"],
            }
        ],
        "fixtures": [],
        "bracket": {"round_of_32": []},
        "third_place_allocation": {"rules": []},
    }


def _canonical_tournament_config() -> dict[str, object]:
    return json.loads(
        (PROJECT_ROOT / "config" / "tournaments" / "2026_world_cup.json").read_text()
    )


def _four_team_group_config() -> dict[str, object]:
    return {
        "schema_version": "test",
        "tournament": {"id": "simple", "name": "Simple Cup"},
        "format": {
            "group_count": 1,
            "teams_per_group": 4,
            "third_place_qualifiers": 1,
            "opening_knockout_matches": 1,
        },
        "teams": [
            {"id": "A1", "name": "Team A1"},
            {"id": "A2", "name": "Team A2"},
            {"id": "A3", "name": "Team A3"},
            {"id": "A4", "name": "Team A4"},
        ],
        "groups": [
            {
                "id": "A",
                "label": "Group A",
                "team_ids": ["A1", "A2", "A3", "A4"],
            }
        ],
        "fixtures": [],
        "bracket": {"round_of_32": []},
        "third_place_allocation": {"rules": []},
    }
