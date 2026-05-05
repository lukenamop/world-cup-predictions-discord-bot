from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from world_cup_bot.data.repositories import (
    ActiveTournamentConfig,
    GuildSettings,
    PredictionEntry,
    PredictionScore,
    StoredMatchResult,
    UserPreferences,
)
from world_cup_bot.domain.predictions import (
    TournamentModel,
    empty_prediction_data,
    next_prediction_step,
    record_group_pick,
    record_knockout_winner,
    record_third_place_qualifiers,
)
from world_cup_bot.services.leaderboard_service import RankedScore, leaderboard_row_text
from world_cup_bot.services.prediction_view_service import (
    PredictionSnapshot,
    PredictionViewService,
    PredictionViewServiceError,
    bracket_render_model,
    group_sheet_render_model,
)

try:
    import PIL  # noqa: F401
except ModuleNotFoundError:
    PIL_AVAILABLE = False
else:
    PIL_AVAILABLE = True


class MilestoneFiveViewTests(unittest.IsolatedAsyncioTestCase):
    def test_privacy_allows_owner_or_shared_full_prediction_views(self) -> None:
        owner_snapshot = _snapshot(viewer_user_id="user-1", share_full_bracket=False)
        other_private = _snapshot(viewer_user_id="user-2", share_full_bracket=False)
        other_shared = _snapshot(viewer_user_id="user-2", share_full_bracket=True)

        self.assertTrue(owner_snapshot.can_view_full_prediction)
        self.assertFalse(other_private.can_view_full_prediction)
        self.assertTrue(other_shared.can_view_full_prediction)

    def test_group_render_model_marks_known_results_without_image_logic(self) -> None:
        snapshot = _snapshot()
        actual_data = {
            "group_rankings": {"A": ["A1", "A2", "A3"]},
            "third_place_qualifier_team_ids": [],
            "knockout": {},
        }

        render_model = group_sheet_render_model(snapshot, actual_data)

        group_a = render_model.groups[0]
        self.assertEqual(group_a.rows[0].status.state, "correct")
        self.assertEqual(group_a.rows[1].status.state, "correct")
        self.assertEqual(group_a.rows[2].third_place_status.state, "pending")

    def test_bracket_render_model_marks_advancement_when_path_differs(self) -> None:
        snapshot = _snapshot()
        actual_data = _actual_data_with_shifted_round_of_32(snapshot.data)

        render_model = bracket_render_model(snapshot, actual_data)

        shifted_match = next(
            match
            for match in render_model.matches
            if match.round_label == "Round of 32" and match.match_id == "R32-1"
        )
        self.assertEqual(shifted_match.winner_team_name, "Team A1")
        self.assertEqual(shifted_match.status.state, "correct")

    def test_bracket_render_model_marks_finished_match_before_round_complete(self) -> None:
        snapshot = _snapshot()
        actual_data = _actual_data_with_partial_round_of_32(snapshot.data)

        render_model = bracket_render_model(snapshot, actual_data)

        decided_match = next(
            match
            for match in render_model.matches
            if match.round_label == "Round of 32" and match.match_id == "R32-1"
        )
        later_match = next(
            match
            for match in render_model.matches
            if match.round_label == "Round of 32" and match.match_id == "R32-2"
        )
        self.assertEqual(decided_match.status.state, "correct")
        self.assertEqual(later_match.status.state, "pending")

    def test_image_renderers_return_png_bytes(self) -> None:
        if not PIL_AVAILABLE:
            self.skipTest("Pillow is not installed in this Python environment.")

        from world_cup_bot.ui.image_renderer import render_bracket_png, render_groups_png

        snapshot = _snapshot()
        actual_data = snapshot.data

        groups_png = render_groups_png(group_sheet_render_model(snapshot, actual_data))
        bracket_png = render_bracket_png(bracket_render_model(snapshot, actual_data))

        self.assertTrue(groups_png.startswith(b"\x89PNG"))
        self.assertTrue(bracket_png.startswith(b"\x89PNG"))

    def test_leaderboard_row_includes_champion_pick(self) -> None:
        ranked = RankedScore(
            rank=1,
            score=_score(),
            champion_team_name="Team A1",
        )

        row = leaderboard_row_text(ranked)

        self.assertIn("Champion: Team A1", row)

    async def test_snapshot_uses_guild_privacy_default_for_missing_preference(self) -> None:
        service = _view_service_with_privacy_default(
            default_share_full_bracket=True,
            preference_share_full_bracket=False,
            preference_updated_at=None,
        )

        snapshot = await service.snapshot(
            guild_id="guild-1",
            target_user_id="user-1",
            viewer_user_id="user-2",
        )

        self.assertTrue(snapshot.can_view_full_prediction)

    async def test_snapshot_keeps_explicit_preference_over_guild_default(self) -> None:
        service = _view_service_with_privacy_default(
            default_share_full_bracket=True,
            preference_share_full_bracket=False,
            preference_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        snapshot = await service.snapshot(
            guild_id="guild-1",
            target_user_id="user-1",
            viewer_user_id="user-2",
        )

        self.assertFalse(snapshot.can_view_full_prediction)

    async def test_actual_data_reports_unresolved_tie_as_view_error(self) -> None:
        config = _prediction_config()
        model = TournamentModel.from_config(config)
        submitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        service = PredictionViewService(pool=None)
        service.tournaments = _FakeTournamentRepository(
            ActiveTournamentConfig(
                id=1,
                tournament_id="test",
                tournament_name="Test Cup",
                schema_version="test",
                config_hash="hash",
                config=config,
                imported_at=submitted_at,
                imported_by_user_id="admin-1",
            )
        )
        service.results = _FakeResultRepository(_unresolved_group_results(model))
        service.tie_breakers = _FakeTieBreakerRepository()

        with self.assertRaises(PredictionViewServiceError) as raised:
            await service.actual_data(
                guild_id="guild-1",
                tournament_config_id=1,
                model=model,
            )

        self.assertIn("official tie-breakers", str(raised.exception))


def _snapshot(
    *,
    viewer_user_id: str = "user-1",
    share_full_bracket: bool = False,
) -> PredictionSnapshot:
    model = TournamentModel.from_config(_prediction_config())
    data = _complete_home_winner_prediction(model)
    submitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    entry = PredictionEntry(
        id=1,
        guild_id="guild-1",
        tournament_config_id=1,
        user_id="user-1",
        display_name="User One",
        draft_data=data,
        submitted_data=data,
        revision=1,
        draft_updated_at=submitted_at,
        submitted_at=submitted_at,
        submitted_updated_at=submitted_at,
    )
    return PredictionSnapshot(
        guild_id="guild-1",
        viewer_user_id=viewer_user_id,
        target_user_id="user-1",
        display_name="User One",
        tournament_name="Test Cup",
        model=model,
        settings=None,
        preferences=UserPreferences(
            guild_id="guild-1",
            user_id="user-1",
            share_full_bracket=share_full_bracket,
        ),
        entry=entry,
        data=data,
        score=None,
        latest_sync_run=None,
        lock_deadline_utc=None,
        is_locked=False,
    )


def _score() -> PredictionScore:
    recalculated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return PredictionScore(
        prediction_entry_id=1,
        guild_id="guild-1",
        tournament_config_id=1,
        user_id="user-1",
        display_name="User One",
        total_points=10,
        group_points=4,
        knockout_points=6,
        breakdown={},
        scoring_version="test",
        recalculated_at=recalculated_at,
    )


def _view_service_with_privacy_default(
    *,
    default_share_full_bracket: bool,
    preference_share_full_bracket: bool,
    preference_updated_at: datetime | None,
) -> PredictionViewService:
    model = TournamentModel.from_config(_prediction_config())
    data = _complete_home_winner_prediction(model)
    submitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service = PredictionViewService(pool=None)
    service.tournaments = _FakeTournamentRepository(
        ActiveTournamentConfig(
            id=1,
            tournament_id="test",
            tournament_name="Test Cup",
            schema_version="test",
            config_hash="hash",
            config=_prediction_config(),
            imported_at=submitted_at,
            imported_by_user_id="admin-1",
        )
    )
    service.predictions = _FakePredictionRepository(
        PredictionEntry(
            id=1,
            guild_id="guild-1",
            tournament_config_id=1,
            user_id="user-1",
            display_name="User One",
            draft_data=data,
            submitted_data=data,
            revision=1,
            draft_updated_at=submitted_at,
            submitted_at=submitted_at,
            submitted_updated_at=submitted_at,
        )
    )
    service.settings = _FakeSettingsRepository(
        GuildSettings(
            guild_id="guild-1",
            timezone="UTC",
            live_results_provider="fifa_public_calendar",
            lock_deadline_utc=None,
            predictions_open=True,
            privacy_defaults={"share_full_bracket": default_share_full_bracket},
        )
    )
    service.preferences = _FakePreferencesRepository(
        UserPreferences(
            guild_id="guild-1",
            user_id="user-1",
            share_full_bracket=preference_share_full_bracket,
            updated_at=preference_updated_at,
        )
    )
    service.results = _FakeResultRepository()
    service.scores = _FakeScoreRepository()
    service.tie_breakers = _FakeTieBreakerRepository()
    return service


def _unresolved_group_results(model: TournamentModel) -> list[StoredMatchResult]:
    results: list[StoredMatchResult] = []
    for group in model.groups:
        match_number = 1
        for home_index, home_team_id in enumerate(group.team_ids):
            for away_team_id in group.team_ids[home_index + 1 :]:
                match_id = f"{group.id}-{match_number}"
                results.append(
                    StoredMatchResult(
                        match_id=match_id,
                        provider="test",
                        provider_match_id=match_id,
                        stage="group",
                        round_name=None,
                        group_id=group.id,
                        home_team_id=home_team_id,
                        away_team_id=away_team_id,
                        home_score=0,
                        away_score=0,
                        status="FINISHED",
                        winner_team_id=None,
                        played_at=None,
                    )
                )
                match_number += 1
    return results


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


def _actual_data_with_shifted_round_of_32(
    prediction_data: dict[str, object],
) -> dict[str, object]:
    actual_data = deepcopy(prediction_data)
    seeded = deepcopy(actual_data["seeded_round_of_32"])
    round_of_32 = deepcopy(actual_data["knockout"]["round_of_32"])

    seeded[0]["home_team_id"] = "B1"
    seeded[1]["home_team_id"] = "A1"
    round_of_32[0]["home_team_id"] = "B1"
    round_of_32[0]["winner_team_id"] = "B1"
    round_of_32[1]["home_team_id"] = "A1"
    round_of_32[1]["winner_team_id"] = "A1"

    actual_data["seeded_round_of_32"] = seeded
    actual_data["knockout"] = {"round_of_32": round_of_32}
    return actual_data


def _actual_data_with_partial_round_of_32(
    prediction_data: dict[str, object],
) -> dict[str, object]:
    actual_data = deepcopy(prediction_data)
    round_of_32 = deepcopy(actual_data["knockout"]["round_of_32"])
    actual_data["knockout"] = {"round_of_32": [round_of_32[0]]}
    return actual_data


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


class _FakeTournamentRepository:
    def __init__(self, tournament: ActiveTournamentConfig) -> None:
        self.tournament = tournament

    async def get_active_config(self, guild_id: str) -> ActiveTournamentConfig:
        return self.tournament


class _FakePredictionRepository:
    def __init__(self, entry: PredictionEntry) -> None:
        self.entry = entry

    async def get_entry(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
    ) -> PredictionEntry:
        return self.entry


class _FakeSettingsRepository:
    def __init__(self, settings: GuildSettings) -> None:
        self.settings = settings

    async def get(self, guild_id: str) -> GuildSettings:
        return self.settings


class _FakePreferencesRepository:
    def __init__(self, preferences: UserPreferences) -> None:
        self.preferences = preferences

    async def get(self, *, guild_id: str, user_id: str) -> UserPreferences:
        return self.preferences


class _FakeResultRepository:
    def __init__(self, results: list[StoredMatchResult] | None = None) -> None:
        self.results = results or []

    async def latest_sync_run(
        self,
        *,
        guild_id: str,
        tournament_config_id: int | None = None,
    ) -> None:
        return None

    async def list_match_results(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[StoredMatchResult]:
        return self.results


class _FakeScoreRepository:
    async def get_user_score(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
    ) -> None:
        return None


class _FakeTieBreakerRepository:
    async def list_for_config(
        self,
        *,
        tournament_id: str,
        config_hash: str,
    ) -> list[object]:
        return []
