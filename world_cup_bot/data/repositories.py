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
    lock_deadline_utc: datetime | None
    predictions_open: bool


class GuildSettingsRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def get(self, guild_id: str) -> GuildSettings | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    guild_id,
                    timezone,
                    live_results_provider,
                    lock_deadline_utc,
                    predictions_open
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
            lock_deadline_utc=row["lock_deadline_utc"],
            predictions_open=row["predictions_open"],
        )

    async def set_predictions_open(
        self,
        *,
        guild_id: str,
        timezone: str,
        live_results_provider: str,
        predictions_open: bool,
    ) -> GuildSettings:
        async with self.pool.acquire() as connection:
            row = await self._set_predictions_open(
                connection,
                guild_id=guild_id,
                timezone=timezone,
                live_results_provider=live_results_provider,
                predictions_open=predictions_open,
            )

        return _row_to_guild_settings(row)

    async def set_predictions_open_with_audit(
        self,
        *,
        guild_id: str,
        timezone: str,
        live_results_provider: str,
        predictions_open: bool,
        actor_user_id: str,
        action: str,
        details: dict[str, object],
    ) -> GuildSettings:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await self._set_predictions_open(
                    connection,
                    guild_id=guild_id,
                    timezone=timezone,
                    live_results_provider=live_results_provider,
                    predictions_open=predictions_open,
                )
                await _insert_audit_log(
                    connection,
                    guild_id=guild_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    details=details,
                )

        return _row_to_guild_settings(row)

    async def _set_predictions_open(
        self,
        connection: Any,
        *,
        guild_id: str,
        timezone: str,
        live_results_provider: str,
        predictions_open: bool,
    ) -> Any:
        return await connection.fetchrow(
            """
            insert into guild_settings (
                guild_id,
                timezone,
                live_results_provider,
                predictions_open
            )
            values ($1, $2, $3, $4)
            on conflict (guild_id) do update set
                predictions_open = excluded.predictions_open,
                updated_at = now()
            returning
                guild_id,
                timezone,
                live_results_provider,
                lock_deadline_utc,
                predictions_open
            """,
            guild_id,
            timezone,
            live_results_provider,
            predictions_open,
        )

    async def set_lock_deadline(
        self,
        *,
        guild_id: str,
        timezone: str,
        live_results_provider: str,
        lock_deadline_utc: datetime | None,
    ) -> GuildSettings:
        async with self.pool.acquire() as connection:
            row = await self._set_lock_deadline(
                connection,
                guild_id=guild_id,
                timezone=timezone,
                live_results_provider=live_results_provider,
                lock_deadline_utc=lock_deadline_utc,
            )

        return _row_to_guild_settings(row)

    async def set_lock_deadline_with_audit(
        self,
        *,
        guild_id: str,
        timezone: str,
        live_results_provider: str,
        lock_deadline_utc: datetime | None,
        actor_user_id: str,
        action: str,
        details: dict[str, object],
    ) -> GuildSettings:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await self._set_lock_deadline(
                    connection,
                    guild_id=guild_id,
                    timezone=timezone,
                    live_results_provider=live_results_provider,
                    lock_deadline_utc=lock_deadline_utc,
                )
                await _insert_audit_log(
                    connection,
                    guild_id=guild_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    details=details,
                )

        return _row_to_guild_settings(row)

    async def _set_lock_deadline(
        self,
        connection: Any,
        *,
        guild_id: str,
        timezone: str,
        live_results_provider: str,
        lock_deadline_utc: datetime | None,
    ) -> Any:
        return await connection.fetchrow(
            """
            insert into guild_settings (
                guild_id,
                timezone,
                live_results_provider,
                lock_deadline_utc
            )
            values ($1, $2, $3, $4)
            on conflict (guild_id) do update set
                lock_deadline_utc = excluded.lock_deadline_utc,
                updated_at = now()
            returning
                guild_id,
                timezone,
                live_results_provider,
                lock_deadline_utc,
                predictions_open
            """,
            guild_id,
            timezone,
            live_results_provider,
            lock_deadline_utc,
        )


def _row_to_guild_settings(row: Any) -> GuildSettings:
    return GuildSettings(
        guild_id=row["guild_id"],
        timezone=row["timezone"],
        live_results_provider=row["live_results_provider"],
        lock_deadline_utc=row["lock_deadline_utc"],
        predictions_open=row["predictions_open"],
    )


async def _insert_audit_log(
    connection: Any,
    *,
    guild_id: str,
    actor_user_id: str,
    action: str,
    details: dict[str, object],
) -> None:
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
        actor_user_id,
        action,
        json.dumps(details, sort_keys=True),
    )


@dataclass(frozen=True)
class TournamentStatus:
    id: int
    tournament_id: str
    tournament_name: str
    schema_version: str
    config_hash: str
    imported_at: datetime
    imported_by_user_id: str | None


@dataclass(frozen=True)
class ActiveTournamentConfig(TournamentStatus):
    config: dict[str, Any]


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
                    tc.id,
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

    async def get_active_config(self, guild_id: str) -> ActiveTournamentConfig | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    tc.id,
                    tc.tournament_id,
                    tc.tournament_name,
                    tc.schema_version,
                    tc.config_hash,
                    tc.config,
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
        return ActiveTournamentConfig(
            id=row["id"],
            tournament_id=row["tournament_id"],
            tournament_name=row["tournament_name"],
            schema_version=row["schema_version"],
            config_hash=row["config_hash"],
            config=_json_dict(row["config"]),
            imported_at=row["imported_at"],
            imported_by_user_id=row["imported_by_user_id"],
        )


def _row_to_tournament_status(row: Any) -> TournamentStatus:
    return TournamentStatus(
        id=row["id"],
        tournament_id=row["tournament_id"],
        tournament_name=row["tournament_name"],
        schema_version=row["schema_version"],
        config_hash=row["config_hash"],
        imported_at=row["imported_at"],
        imported_by_user_id=row["imported_by_user_id"],
    )


@dataclass(frozen=True)
class PredictionEntry:
    id: int
    guild_id: str
    tournament_config_id: int
    user_id: str
    display_name: str
    draft_data: dict[str, Any]
    submitted_data: dict[str, Any] | None
    revision: int
    draft_updated_at: datetime | None
    submitted_at: datetime | None
    submitted_updated_at: datetime | None


class PredictionRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def get_entry(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
    ) -> PredictionEntry | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    id,
                    guild_id,
                    tournament_config_id,
                    user_id,
                    display_name,
                    draft_data,
                    submitted_data,
                    revision,
                    draft_updated_at,
                    submitted_at,
                    submitted_updated_at
                from prediction_entries
                where guild_id = $1
                    and tournament_config_id = $2
                    and user_id = $3
                """,
                guild_id,
                tournament_config_id,
                user_id,
            )

        if row is None:
            return None
        return _row_to_prediction_entry(row)

    async def save_draft(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
        display_name: str,
        data: dict[str, Any],
    ) -> PredictionEntry:
        return await self._write_entry(
            guild_id=guild_id,
            tournament_config_id=tournament_config_id,
            user_id=user_id,
            display_name=display_name,
            data=data,
            event_type="draft_saved",
        )

    async def submit_draft(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
        display_name: str,
        data: dict[str, Any],
    ) -> PredictionEntry:
        return await self._write_entry(
            guild_id=guild_id,
            tournament_config_id=tournament_config_id,
            user_id=user_id,
            display_name=display_name,
            data=data,
            event_type="submitted",
        )

    async def _write_entry(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
        display_name: str,
        data: dict[str, Any],
        event_type: str,
    ) -> PredictionEntry:
        data_json = json.dumps(data, sort_keys=True)
        is_submit = event_type == "submitted"

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    insert into prediction_entries (
                        guild_id,
                        tournament_config_id,
                        user_id,
                        display_name,
                        draft_data,
                        submitted_data,
                        submitted_at,
                        submitted_updated_at,
                        revision
                    )
                    values (
                        $1,
                        $2,
                        $3,
                        $4,
                        $5::jsonb,
                        case when $6 then $5::jsonb else null end,
                        case when $6 then now() else null end,
                        case when $6 then now() else null end,
                        1
                    )
                    on conflict (guild_id, tournament_config_id, user_id)
                    do update set
                        display_name = excluded.display_name,
                        draft_data = excluded.draft_data,
                        draft_updated_at = now(),
                        submitted_data = case
                            when $6 then excluded.draft_data
                            else prediction_entries.submitted_data
                        end,
                        submitted_at = case
                            when $6 then coalesce(prediction_entries.submitted_at, now())
                            else prediction_entries.submitted_at
                        end,
                        submitted_updated_at = case
                            when $6 then now()
                            else prediction_entries.submitted_updated_at
                        end,
                        revision = prediction_entries.revision + 1
                    returning
                        id,
                        guild_id,
                        tournament_config_id,
                        user_id,
                        display_name,
                        draft_data,
                        submitted_data,
                        revision,
                        draft_updated_at,
                        submitted_at,
                        submitted_updated_at
                    """,
                    guild_id,
                    tournament_config_id,
                    user_id,
                    display_name,
                    data_json,
                    is_submit,
                )
                await connection.execute(
                    """
                    insert into prediction_history (
                        prediction_entry_id,
                        revision,
                        event_type,
                        actor_user_id,
                        data
                    )
                    values ($1, $2, $3, $4, $5::jsonb)
                    """,
                    row["id"],
                    row["revision"],
                    event_type,
                    user_id,
                    data_json,
                )

        return _row_to_prediction_entry(row)


def _row_to_prediction_entry(row: Any) -> PredictionEntry:
    return PredictionEntry(
        id=row["id"],
        guild_id=row["guild_id"],
        tournament_config_id=row["tournament_config_id"],
        user_id=row["user_id"],
        display_name=row["display_name"],
        draft_data=_json_dict(row["draft_data"]),
        submitted_data=_json_dict(row["submitted_data"]) if row["submitted_data"] else None,
        revision=row["revision"],
        draft_updated_at=row["draft_updated_at"],
        submitted_at=row["submitted_at"],
        submitted_updated_at=row["submitted_updated_at"],
    )


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        decoded = json.loads(value)
        return dict(decoded) if isinstance(decoded, dict) else {}
    return dict(value) if value else {}
