from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from world_cup_bot.domain.tournament import TournamentSummary


@dataclass(frozen=True)
class GuildSettings:
    guild_id: str
    timezone: str
    live_results_provider: str
    predictions_open: bool


class GuildSettingsRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def get(self, guild_id: str) -> GuildSettings | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select guild_id, timezone, live_results_provider, predictions_open
                from guild_settings
                where guild_id = $1
                """,
                guild_id,
            )

        if row is None:
            return None

        return GuildSettings(
            guild_id=row["guild_id"],
            timezone=row["timezone"],
            live_results_provider=row["live_results_provider"],
            predictions_open=row["predictions_open"],
        )


@dataclass(frozen=True)
class TournamentStatus:
    tournament_id: str
    tournament_name: str
    schema_version: str
    config_hash: str
    imported_at: datetime
    imported_by_user_id: str | None


class TournamentConfigRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def save_active_import(
        self,
        *,
        guild_id: str,
        imported_by_user_id: str,
        summary: TournamentSummary,
        config_hash: str,
        config: dict[str, Any],
    ) -> TournamentStatus:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    insert into tournament_configs (
                        guild_id,
                        tournament_id,
                        tournament_name,
                        schema_version,
                        config_hash,
                        config,
                        imported_by_user_id
                    )
                    values ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    on conflict (guild_id, tournament_id, config_hash) do update set
                        imported_by_user_id = excluded.imported_by_user_id,
                        imported_at = now()
                    returning
                        id,
                        tournament_id,
                        tournament_name,
                        schema_version,
                        config_hash,
                        imported_at,
                        imported_by_user_id
                    """,
                    guild_id,
                    summary.tournament_id,
                    summary.name,
                    summary.schema_version,
                    config_hash,
                    json.dumps(config, sort_keys=True),
                    imported_by_user_id,
                )
                await connection.execute(
                    """
                    insert into guild_tournament_state (
                        guild_id,
                        active_tournament_config_id
                    )
                    values ($1, $2)
                    on conflict (guild_id) do update set
                        active_tournament_config_id = excluded.active_tournament_config_id,
                        updated_at = now()
                    """,
                    guild_id,
                    row["id"],
                )
                await connection.execute(
                    """
                    insert into audit_log (
                        guild_id,
                        actor_user_id,
                        action,
                        details
                    )
                    values ($1, $2, $3, $4::jsonb)
                    """,
                    guild_id,
                    imported_by_user_id,
                    "tournament_imported",
                    json.dumps(
                        {
                            "tournament_id": summary.tournament_id,
                            "schema_version": summary.schema_version,
                            "config_hash": config_hash,
                        },
                        sort_keys=True,
                    ),
                )

        return _row_to_tournament_status(row)

    async def get_active(self, guild_id: str) -> TournamentStatus | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    tc.tournament_id,
                    tc.tournament_name,
                    tc.schema_version,
                    tc.config_hash,
                    tc.imported_at,
                    tc.imported_by_user_id
                from guild_tournament_state gts
                join tournament_configs tc
                    on tc.id = gts.active_tournament_config_id
                where gts.guild_id = $1
                """,
                guild_id,
            )

        if row is None:
            return None
        return _row_to_tournament_status(row)


def _row_to_tournament_status(row: Any) -> TournamentStatus:
    return TournamentStatus(
        tournament_id=row["tournament_id"],
        tournament_name=row["tournament_name"],
        schema_version=row["schema_version"],
        config_hash=row["config_hash"],
        imported_at=row["imported_at"],
        imported_by_user_id=row["imported_by_user_id"],
    )
