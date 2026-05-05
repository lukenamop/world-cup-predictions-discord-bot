from __future__ import annotations

from datetime import datetime, timezone
import unittest

from world_cup_bot.data.repositories import ActiveTournamentConfig, GuildSettings, PredictionEntry
from world_cup_bot.domain.locks import effective_lock_deadline, is_prediction_locked
from world_cup_bot.domain.predictions import (
    TournamentModel,
    empty_prediction_data,
    is_submission_complete,
    next_prediction_step,
    prediction_summary,
    record_group_pick,
    record_knockout_winner,
    record_third_place_qualifiers,
)
from world_cup_bot.services.prediction_service import (
    PredictionService,
    PredictionServiceError,
    PredictionSessionState,
)


class PredictionServiceWriteValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_rechecks_predictions_are_open(self) -> None:
        tournament = _active_tournament(1)
        service, predictions = _prediction_service(
            settings=_guild_settings(predictions_open=False),
            tournament=tournament,
        )

        with self.assertRaisesRegex(PredictionServiceError, "closed"):
            await service.submit(
                state=_session_state(tournament),
                user_id="user-1",
                display_name="User One",
                data=empty_prediction_data(),
            )

        self.assertEqual(predictions.submit_calls, 0)

    async def test_submit_rechecks_current_lock_deadline(self) -> None:
        tournament = _active_tournament(1)
        service, predictions = _prediction_service(
            settings=_guild_settings(
                lock_deadline_utc=datetime(2020, 1, 1, tzinfo=timezone.utc)
            ),
            tournament=tournament,
        )

        with self.assertRaisesRegex(PredictionServiceError, "locked"):
            await service.submit(
                state=_session_state(tournament),
                user_id="user-1",
                display_name="User One",
                data=empty_prediction_data(),
            )

        self.assertEqual(predictions.submit_calls, 0)

    async def test_submit_rechecks_active_tournament_before_validation(self) -> None:
        original_tournament = _active_tournament(1)
        service, predictions = _prediction_service(
            settings=_guild_settings(),
            tournament=_active_tournament(2),
        )

        with self.assertRaisesRegex(PredictionServiceError, "Tournament data changed"):
            await service.submit(
                state=_session_state(original_tournament),
                user_id="user-1",
                display_name="User One",
                data=empty_prediction_data(),
            )

        self.assertEqual(predictions.submit_calls, 0)

    async def test_predict_rejects_existing_submitted_prediction(self) -> None:
        service, _predictions = _prediction_service(
            settings=_guild_settings(),
            tournament=_active_tournament(1),
            entry=_prediction_entry(submitted_data={"submitted": True}),
        )

        with self.assertRaisesRegex(PredictionServiceError, "already have"):
            await service.start_prediction(
                guild_id="guild-1",
                user_id="user-1",
                edit_existing=False,
            )

    async def test_edit_requires_existing_submitted_prediction(self) -> None:
        service, _predictions = _prediction_service(
            settings=_guild_settings(),
            tournament=_active_tournament(1),
            entry=None,
        )

        with self.assertRaisesRegex(PredictionServiceError, "do not have"):
            await service.start_prediction(
                guild_id="guild-1",
                user_id="user-1",
                edit_existing=True,
            )

    async def test_edit_uses_submitted_data_not_unsubmitted_draft_data(self) -> None:
        service, _predictions = _prediction_service(
            settings=_guild_settings(),
            tournament=_active_tournament(1),
            entry=_prediction_entry(
                draft_data={"unsubmitted": True},
                submitted_data={"submitted": True},
            ),
        )

        state = await service.start_prediction(
            guild_id="guild-1",
            user_id="user-1",
            edit_existing=True,
        )

        self.assertEqual(state.data, {"submitted": True})

    async def test_predict_ignores_legacy_unsubmitted_draft_rows(self) -> None:
        service, _predictions = _prediction_service(
            settings=_guild_settings(),
            tournament=_active_tournament(1),
            entry=_prediction_entry(
                draft_data={"unsubmitted": True},
                submitted_data=None,
            ),
        )

        state = await service.start_prediction(
            guild_id="guild-1",
            user_id="user-1",
            edit_existing=False,
        )

        self.assertEqual(state.data, empty_prediction_data())

    async def test_submit_rejects_stale_predict_session_after_submission_exists(self) -> None:
        service, predictions = _prediction_service(
            settings=_guild_settings(),
            tournament=_active_tournament(1),
            entry=None,
        )
        state = await service.start_prediction(
            guild_id="guild-1",
            user_id="user-1",
            edit_existing=False,
        )
        predictions.entry = _prediction_entry(submitted_data={"submitted": True})

        with self.assertRaisesRegex(PredictionServiceError, "already have"):
            await service.submit(
                state=state,
                user_id="user-1",
                display_name="User One",
                data=_completed_prediction_data(state.model),
            )

        self.assertEqual(predictions.submit_calls, 0)

    async def test_submit_allows_edit_session_when_submission_exists(self) -> None:
        service, predictions = _prediction_service(
            settings=_guild_settings(),
            tournament=_active_tournament(1),
            entry=_prediction_entry(submitted_data={"submitted": True}),
        )
        state = await service.start_prediction(
            guild_id="guild-1",
            user_id="user-1",
            edit_existing=True,
        )

        await service.submit(
            state=state,
            user_id="user-1",
            display_name="User One",
            data=_completed_prediction_data(state.model),
        )

        self.assertEqual(predictions.submit_calls, 1)


class PredictionFlowTests(unittest.TestCase):
    def test_prediction_flow_seeds_knockout_and_reaches_final_placements(self) -> None:
        model = TournamentModel.from_config(_prediction_config())
        data = _completed_prediction_data(model)

        self.assertTrue(is_submission_complete(model, data))
        self.assertEqual(data["seeded_round_of_32"][12]["home_team_id"], "A3")
        self.assertEqual(data["seeded_round_of_32"][15]["away_team_id"], "H3")

        summary = prediction_summary(model, data)

        self.assertEqual(summary.champion_team_id, "A1")
        self.assertEqual(summary.runner_up_team_id, "I1")
        self.assertEqual(summary.third_place_team_id, "E1")
        self.assertEqual(summary.fourth_place_team_id, "A3")

    def test_prediction_lock_uses_configured_deadline_or_first_kickoff(self) -> None:
        config = {
            "fixtures": [
                {"kickoff_utc": "2026-06-12T00:00:00Z"},
                {"kickoff_utc": "2026-06-11T18:00:00+00:00"},
            ]
        }

        self.assertEqual(
            effective_lock_deadline(
                configured_deadline_utc=None,
                tournament_config=config,
            ),
            datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(
            is_prediction_locked(
                configured_deadline_utc=None,
                tournament_config=config,
                now_utc=datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
            )
        )
        self.assertFalse(
            is_prediction_locked(
                configured_deadline_utc=datetime(2026, 6, 12, 0, 0, tzinfo=timezone.utc),
                tournament_config=config,
                now_utc=datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
            )
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


def _guild_settings(
    *,
    predictions_open: bool = True,
    lock_deadline_utc: datetime | None = None,
) -> GuildSettings:
    return GuildSettings(
        guild_id="guild-1",
        timezone="UTC",
        live_results_provider="football_data_org",
        lock_deadline_utc=lock_deadline_utc,
        predictions_open=predictions_open,
    )


def _active_tournament(identifier: int) -> ActiveTournamentConfig:
    return ActiveTournamentConfig(
        id=identifier,
        tournament_id="test",
        tournament_name="Test Cup",
        schema_version="test",
        config_hash=f"hash-{identifier}",
        config=_prediction_config(),
        imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        imported_by_user_id="admin-1",
    )


def _session_state(tournament: ActiveTournamentConfig) -> PredictionSessionState:
    settings = _guild_settings()
    return PredictionSessionState(
        guild_id="guild-1",
        tournament=tournament,
        model=TournamentModel.from_config(tournament.config),
        settings=settings,
        entry=None,
        data=empty_prediction_data(),
        lock_deadline_utc=None,
        edit_existing=False,
    )


def _completed_prediction_data(model: TournamentModel) -> dict[str, object]:
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


def _prediction_entry(
    *,
    draft_data: dict[str, object] | None = None,
    submitted_data: dict[str, object] | None = None,
) -> PredictionEntry:
    return PredictionEntry(
        id=1,
        guild_id="guild-1",
        tournament_config_id=1,
        user_id="user-1",
        display_name="User One",
        draft_data=draft_data or {},
        submitted_data=submitted_data,
        revision=1,
        draft_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        submitted_at=(
            datetime(2026, 1, 1, tzinfo=timezone.utc)
            if submitted_data is not None
            else None
        ),
        submitted_updated_at=(
            datetime(2026, 1, 1, tzinfo=timezone.utc)
            if submitted_data is not None
            else None
        ),
    )


def _prediction_service(
    *,
    settings: GuildSettings,
    tournament: ActiveTournamentConfig | None,
    entry: PredictionEntry | None = None,
) -> tuple[PredictionService, "_FakePredictionRepository"]:
    service = PredictionService(pool=None)
    predictions = _FakePredictionRepository(entry)
    service.settings = _FakeSettingsRepository(settings)
    service.tournaments = _FakeTournamentRepository(tournament)
    service.predictions = predictions
    return service, predictions


class _FakeSettingsRepository:
    def __init__(self, settings: GuildSettings | None) -> None:
        self.settings = settings

    async def get(self, guild_id: str) -> GuildSettings | None:
        return self.settings


class _FakeTournamentRepository:
    def __init__(self, tournament: ActiveTournamentConfig | None) -> None:
        self.tournament = tournament

    async def get_active_config(self, guild_id: str) -> ActiveTournamentConfig | None:
        return self.tournament


class _FakePredictionRepository:
    def __init__(self, entry: PredictionEntry | None = None) -> None:
        self.entry = entry
        self.submit_calls = 0

    async def get_entry(self, **kwargs: object) -> PredictionEntry | None:
        return self.entry

    async def submit_prediction(self, **kwargs: object) -> None:
        self.submit_calls += 1
