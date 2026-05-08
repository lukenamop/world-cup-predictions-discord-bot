from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from typing import Any

from world_cup_bot.data.repositories import (
    GuildSettingsRepository,
    PredictionRepository,
    PredictionScoreRepository,
    ResultRepository,
    TournamentConfigRepository,
)


class ExportServiceError(RuntimeError):
    """Raised when export data is not available."""


class ExportService:
    def __init__(self, pool: Any) -> None:
        self.settings = GuildSettingsRepository(pool)
        self.tournaments = TournamentConfigRepository(pool)
        self.predictions = PredictionRepository(pool)
        self.results = ResultRepository(pool)
        self.scores = PredictionScoreRepository(pool)

    async def prediction_export(self, *, guild_id: str) -> tuple[str, bytes]:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise ExportServiceError("Ask an admin to import tournament data first.")

        entries = await self.predictions.list_submitted_entries(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        scores = await self.scores.list_scores(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        score_by_entry_id = {score.prediction_entry_id: score for score in scores}
        payload = {
            "exported_at": datetime.now(timezone.utc),
            "guild_id": guild_id,
            "tournament": _public_tournament(tournament),
            "prediction_count": len(entries),
            "predictions": [
                {
                    "entry": _submitted_prediction_entry(entry),
                    "score": _jsonable(score_by_entry_id.get(entry.id)),
                }
                for entry in entries
            ],
        }
        return _filename("predictions", guild_id), _json_bytes(payload)

    async def backup(self, *, guild_id: str) -> tuple[str, bytes]:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise ExportServiceError("Ask an admin to import tournament data first.")

        settings = await self.settings.get(guild_id)
        entries = await self.predictions.list_entries(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        scores = await self.scores.list_scores(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        results = await self.results.list_match_results(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        sync_runs = await self.results.list_sync_runs(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )
        payload = {
            "backup_created_at": datetime.now(timezone.utc),
            "guild_id": guild_id,
            "settings": _jsonable(settings),
            "active_tournament": _jsonable(tournament),
            "prediction_entries": [_jsonable(entry) for entry in entries],
            "prediction_scores": [_jsonable(score) for score in scores],
            "match_results": [_jsonable(result) for result in results],
            "recent_sync_runs": [_jsonable(run) for run in sync_runs],
        }
        return _filename("backup", guild_id), _json_bytes(payload)


def _public_tournament(tournament: Any) -> dict[str, Any]:
    return {
        "id": tournament.id,
        "tournament_id": tournament.tournament_id,
        "tournament_name": tournament.tournament_name,
        "schema_version": tournament.schema_version,
        "config_hash": tournament.config_hash,
    }


def _submitted_prediction_entry(entry: Any) -> dict[str, Any]:
    return _jsonable(
        {
            "id": entry.id,
            "guild_id": entry.guild_id,
            "tournament_config_id": entry.tournament_config_id,
            "user_id": entry.user_id,
            "display_name": entry.display_name,
            "submitted_data": entry.submitted_data,
            "submitted_at": entry.submitted_at,
            "submitted_updated_at": entry.submitted_updated_at,
        }
    )


def _filename(kind: str, guild_id: str) -> str:
    today = date.today().isoformat()
    return f"world-cup-{kind}-{guild_id}-{today}.json"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(_jsonable(payload), indent=2, sort_keys=True).encode("utf-8")


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
