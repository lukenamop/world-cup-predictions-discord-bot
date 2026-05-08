from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from world_cup_bot.data.repositories import (
    ActiveTournamentConfig,
    PredictionEntry,
)
from world_cup_bot.services.export_service import ExportService


class ExportServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_prediction_export_omits_unsubmitted_draft_data(self) -> None:
        service = ExportService(pool=None)
        service.tournaments = _TournamentRepository()
        service.predictions = _PredictionRepository()
        service.scores = _EmptyRepository()

        _filename, content = await service.prediction_export(guild_id="guild-1")

        payload = json.loads(content)
        entry = payload["predictions"][0]["entry"]
        self.assertNotIn("draft_data", entry)
        self.assertNotIn("draft_updated_at", entry)
        self.assertNotIn("revision", entry)
        self.assertEqual(entry["submitted_data"], {"winner": "submitted-pick"})

    async def test_backup_omits_removed_user_preferences(self) -> None:
        service = ExportService(pool=None)
        service.tournaments = _TournamentRepository()
        service.settings = _EmptyRepository()
        service.predictions = _EmptyRepository()
        service.scores = _EmptyRepository()
        service.results = _EmptyRepository()

        _filename, content = await service.backup(guild_id="guild-1")

        payload = json.loads(content)
        self.assertNotIn("user_preferences", payload)


class _TournamentRepository:
    async def get_active_config(self, guild_id: str) -> ActiveTournamentConfig | None:
        return ActiveTournamentConfig(
            id=1,
            tournament_id="test",
            tournament_name="Test Cup",
            schema_version="test",
            config_hash="abc123",
            imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            imported_by_user_id="admin-1",
            config={},
        )


class _PredictionRepository:
    async def list_submitted_entries(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[PredictionEntry]:
        submitted_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        return [
            PredictionEntry(
                id=1,
                guild_id=guild_id,
                tournament_config_id=tournament_config_id,
                user_id="user-1",
                display_name="User One",
                draft_data={"winner": "draft-pick"},
                submitted_data={"winner": "submitted-pick"},
                revision=2,
                draft_updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                submitted_at=submitted_at,
                submitted_updated_at=submitted_at,
            )
        ]


class _EmptyRepository:
    async def get(self, guild_id: str) -> None:
        return None

    async def list_submitted_entries(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[object]:
        return []

    async def list_entries(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[object]:
        return []

    async def list_scores(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[object]:
        return []

    async def list_match_results(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[object]:
        return []

    async def list_sync_runs(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[object]:
        return []
