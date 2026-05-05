from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from world_cup_bot.domain.standings import StandingAdjudication
from world_cup_bot.domain.tournament import TournamentSummary


@dataclass(frozen=True)
class GuildSettings:
    guild_id: str
    timezone: str
    live_results_provider: str
    lock_deadline_utc: datetime | None
    predictions_open: bool
    announcement_channel_id: str | None = None
    leaderboard_channel_id: str | None = None
    scoring_rules: dict[str, Any] = field(default_factory=dict)
    privacy_defaults: dict[str, Any] = field(default_factory=dict)
    lock_mode: str = "full_bracket_lock"


@dataclass(frozen=True)
class UserPreferences:
    guild_id: str
    user_id: str
    share_full_bracket: bool
    updated_at: datetime | None = None


class GuildSettingsRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def get(self, guild_id: str) -> GuildSettings | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    guild_id,
                    announcement_channel_id,
                    leaderboard_channel_id,
                    timezone,
                    live_results_provider,
                    lock_deadline_utc,
                    predictions_open,
                    scoring_rules,
                    privacy_defaults,
                    lock_mode
                from guild_settings
                where guild_id = $1
                """,
                guild_id,
            )

        if row is None:
            return None

        return GuildSettings(
            guild_id=row["guild_id"],
            announcement_channel_id=row["announcement_channel_id"],
            leaderboard_channel_id=row["leaderboard_channel_id"],
            timezone=row["timezone"],
            live_results_provider=row["live_results_provider"],
            lock_deadline_utc=row["lock_deadline_utc"],
            predictions_open=row["predictions_open"],
            scoring_rules=_json_dict(row["scoring_rules"]),
            privacy_defaults=_json_dict(row["privacy_defaults"]),
            lock_mode=row["lock_mode"],
        )

    async def save_settings_with_audit(
        self,
        *,
        settings: GuildSettings,
        actor_user_id: str,
        action: str,
        details: dict[str, object],
    ) -> GuildSettings:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    insert into guild_settings (
                        guild_id,
                        announcement_channel_id,
                        leaderboard_channel_id,
                        timezone,
                        scoring_rules,
                        privacy_defaults,
                        live_results_provider,
                        lock_mode,
                        lock_deadline_utc,
                        predictions_open
                    )
                    values (
                        $1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10
                    )
                    on conflict (guild_id) do update set
                        announcement_channel_id = excluded.announcement_channel_id,
                        leaderboard_channel_id = excluded.leaderboard_channel_id,
                        timezone = excluded.timezone,
                        scoring_rules = excluded.scoring_rules,
                        privacy_defaults = excluded.privacy_defaults,
                        live_results_provider = excluded.live_results_provider,
                        lock_mode = excluded.lock_mode,
                        lock_deadline_utc = excluded.lock_deadline_utc,
                        predictions_open = excluded.predictions_open,
                        updated_at = now()
                    returning
                        guild_id,
                        announcement_channel_id,
                        leaderboard_channel_id,
                        timezone,
                        live_results_provider,
                        lock_deadline_utc,
                        predictions_open,
                        scoring_rules,
                        privacy_defaults,
                        lock_mode
                    """,
                    settings.guild_id,
                    settings.announcement_channel_id,
                    settings.leaderboard_channel_id,
                    settings.timezone,
                    json.dumps(settings.scoring_rules, sort_keys=True),
                    json.dumps(settings.privacy_defaults, sort_keys=True),
                    settings.live_results_provider,
                    settings.lock_mode,
                    settings.lock_deadline_utc,
                    settings.predictions_open,
                )
                await _insert_audit_log(
                    connection,
                    guild_id=settings.guild_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    details=details,
                )

        return _row_to_guild_settings(row)

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
                announcement_channel_id,
                leaderboard_channel_id,
                timezone,
                live_results_provider,
                lock_deadline_utc,
                predictions_open,
                scoring_rules,
                privacy_defaults,
                lock_mode
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
                announcement_channel_id,
                leaderboard_channel_id,
                timezone,
                live_results_provider,
                lock_deadline_utc,
                predictions_open,
                scoring_rules,
                privacy_defaults,
                lock_mode
            """,
            guild_id,
            timezone,
            live_results_provider,
            lock_deadline_utc,
        )


class UserPreferencesRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def list_for_guild(self, *, guild_id: str) -> list[UserPreferences]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select
                    guild_id,
                    user_id,
                    share_full_bracket,
                    updated_at
                from user_preferences
                where guild_id = $1
                order by user_id asc
                """,
                guild_id,
            )

        return [_row_to_user_preferences(row) for row in rows]

    async def get(self, *, guild_id: str, user_id: str) -> UserPreferences:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    guild_id,
                    user_id,
                    share_full_bracket,
                    updated_at
                from user_preferences
                where guild_id = $1
                    and user_id = $2
                """,
                guild_id,
                user_id,
            )

        if row is None:
            return UserPreferences(
                guild_id=guild_id,
                user_id=user_id,
                share_full_bracket=False,
                updated_at=None,
            )
        return _row_to_user_preferences(row)

    async def set_share_full_bracket(
        self,
        *,
        guild_id: str,
        user_id: str,
        share_full_bracket: bool,
    ) -> UserPreferences:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                insert into user_preferences (
                    guild_id,
                    user_id,
                    share_full_bracket
                )
                values ($1, $2, $3)
                on conflict (guild_id, user_id) do update set
                    share_full_bracket = excluded.share_full_bracket,
                    updated_at = now()
                returning
                    guild_id,
                    user_id,
                    share_full_bracket,
                    updated_at
                """,
                guild_id,
                user_id,
                share_full_bracket,
            )

        return _row_to_user_preferences(row)


def _row_to_user_preferences(row: Any) -> UserPreferences:
    return UserPreferences(
        guild_id=row["guild_id"],
        user_id=row["user_id"],
        share_full_bracket=row["share_full_bracket"],
        updated_at=row["updated_at"],
    )


def _row_to_guild_settings(row: Any) -> GuildSettings:
    return GuildSettings(
        guild_id=row["guild_id"],
        announcement_channel_id=row["announcement_channel_id"],
        leaderboard_channel_id=row["leaderboard_channel_id"],
        timezone=row["timezone"],
        live_results_provider=row["live_results_provider"],
        lock_deadline_utc=row["lock_deadline_utc"],
        predictions_open=row["predictions_open"],
        scoring_rules=_json_dict(row["scoring_rules"]),
        privacy_defaults=_json_dict(row["privacy_defaults"]),
        lock_mode=row["lock_mode"],
    )


async def _insert_audit_log(
    connection: Any,
    *,
    guild_id: str | None,
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


class AuditLogRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def insert(
        self,
        *,
        guild_id: str | None,
        actor_user_id: str,
        action: str,
        details: dict[str, object],
    ) -> None:
        async with self.pool.acquire() as connection:
            await _insert_audit_log(
                connection,
                guild_id=guild_id,
                actor_user_id=actor_user_id,
                action=action,
                details=details,
            )


@dataclass(frozen=True)
class StoredTieBreakerAdjudication:
    id: int
    tournament_id: str
    config_hash: str
    scope: str
    scope_key: str
    team_ids: tuple[str, ...]
    ordered_team_ids: tuple[str, ...]
    criterion: str
    reason: str
    actor_user_id: str
    created_at: datetime

    def to_domain(self) -> StandingAdjudication:
        return StandingAdjudication(
            scope="best_third" if self.scope == "best_third" else "group",
            group_id=None if self.scope == "best_third" else self.scope_key,
            ordered_team_ids=self.ordered_team_ids,
            criterion=self.criterion,
            reason=self.reason,
        )


class TieBreakerAdjudicationRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def list_for_config(
        self,
        *,
        tournament_id: str,
        config_hash: str,
    ) -> list[StoredTieBreakerAdjudication]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select
                    id,
                    tournament_id,
                    config_hash,
                    scope,
                    scope_key,
                    team_ids,
                    ordered_team_ids,
                    criterion,
                    reason,
                    actor_user_id,
                    created_at
                from tie_breaker_adjudications
                where tournament_id = $1
                    and config_hash = $2
                order by created_at asc, id asc
                """,
                tournament_id,
                config_hash,
            )
        return [_row_to_tie_breaker_adjudication(row) for row in rows]

    async def save_with_audit(
        self,
        *,
        tournament_id: str,
        config_hash: str,
        scope: str,
        scope_key: str,
        team_ids: Sequence[str],
        ordered_team_ids: Sequence[str],
        criterion: str,
        reason: str,
        actor_user_id: str,
    ) -> StoredTieBreakerAdjudication:
        sorted_team_ids = tuple(sorted(str(team_id) for team_id in team_ids))
        team_set_key = ",".join(sorted_team_ids)
        ordered = tuple(str(team_id) for team_id in ordered_team_ids)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                previous = await connection.fetchrow(
                    """
                    select
                        id,
                        tournament_id,
                        config_hash,
                        scope,
                        scope_key,
                        team_ids,
                        ordered_team_ids,
                        criterion,
                        reason,
                        actor_user_id,
                        created_at
                    from tie_breaker_adjudications
                    where tournament_id = $1
                        and config_hash = $2
                        and scope = $3
                        and scope_key = $4
                        and team_set_key = $5
                    """,
                    tournament_id,
                    config_hash,
                    scope,
                    scope_key,
                    team_set_key,
                )
                row = await connection.fetchrow(
                    """
                    insert into tie_breaker_adjudications (
                        tournament_id,
                        config_hash,
                        scope,
                        scope_key,
                        team_set_key,
                        team_ids,
                        ordered_team_ids,
                        criterion,
                        reason,
                        actor_user_id
                    )
                    values (
                        $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb,
                        $8, $9, $10
                    )
                    on conflict (
                        tournament_id,
                        config_hash,
                        scope,
                        scope_key,
                        team_set_key
                    ) do update set
                        team_ids = excluded.team_ids,
                        ordered_team_ids = excluded.ordered_team_ids,
                        criterion = excluded.criterion,
                        reason = excluded.reason,
                        actor_user_id = excluded.actor_user_id,
                        created_at = now()
                    returning
                        id,
                        tournament_id,
                        config_hash,
                        scope,
                        scope_key,
                        team_ids,
                        ordered_team_ids,
                        criterion,
                        reason,
                        actor_user_id,
                        created_at
                    """,
                    tournament_id,
                    config_hash,
                    scope,
                    scope_key,
                    team_set_key,
                    json.dumps(list(sorted_team_ids), sort_keys=True),
                    json.dumps(list(ordered), sort_keys=True),
                    criterion,
                    reason,
                    actor_user_id,
                )
                await _insert_audit_log(
                    connection,
                    guild_id=None,
                    actor_user_id=actor_user_id,
                    action="tie_breaker_adjudicated",
                    details={
                        "tournament_id": tournament_id,
                        "config_hash": config_hash,
                        "scope": scope,
                        "scope_key": scope_key,
                        "team_ids": list(sorted_team_ids),
                        "ordered_team_ids": list(ordered),
                        "criterion": criterion,
                        "reason": reason,
                        "previous": (
                            _tie_breaker_audit_snapshot(previous)
                            if previous
                            else None
                        ),
                    },
                )

        return _row_to_tie_breaker_adjudication(row)


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


@dataclass(frozen=True)
class GuildActiveTournamentConfig(ActiveTournamentConfig):
    guild_id: str


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

    async def list_active_guild_ids(self) -> list[str]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select guild_id
                from guild_tournament_state
                order by guild_id
                """
            )
        return [row["guild_id"] for row in rows]

    async def list_active_configs(self) -> list[GuildActiveTournamentConfig]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select
                    gts.guild_id,
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
                order by gts.guild_id
                """
            )

        return [
            GuildActiveTournamentConfig(
                id=row["id"],
                guild_id=row["guild_id"],
                tournament_id=row["tournament_id"],
                tournament_name=row["tournament_name"],
                schema_version=row["schema_version"],
                config_hash=row["config_hash"],
                config=_json_dict(row["config"]),
                imported_at=row["imported_at"],
                imported_by_user_id=row["imported_by_user_id"],
            )
            for row in rows
        ]


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

    async def list_submitted_entries(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[PredictionEntry]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
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
                    and submitted_data is not null
                order by submitted_at asc, id asc
                """,
                guild_id,
                tournament_config_id,
            )

        return [_row_to_prediction_entry(row) for row in rows]

    async def list_entries(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[PredictionEntry]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
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
                order by submitted_at asc nulls last, draft_updated_at asc, id asc
                """,
                guild_id,
                tournament_config_id,
            )

        return [_row_to_prediction_entry(row) for row in rows]

    async def submit_prediction(
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
        )

    async def _write_entry(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
        display_name: str,
        data: dict[str, Any],
    ) -> PredictionEntry:
        data_json = json.dumps(data, sort_keys=True)

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
                        $5::jsonb,
                        now(),
                        now(),
                        1
                    )
                    on conflict (guild_id, tournament_config_id, user_id)
                    do update set
                        display_name = excluded.display_name,
                        draft_data = excluded.draft_data,
                        draft_updated_at = now(),
                        submitted_data = excluded.draft_data,
                        submitted_at = coalesce(prediction_entries.submitted_at, now()),
                        submitted_updated_at = now(),
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
                    "submitted",
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


@dataclass(frozen=True)
class StoredMatchResult:
    match_id: str
    provider: str
    provider_match_id: str | None
    stage: str
    round_name: str | None
    group_id: str | None
    home_team_id: str
    away_team_id: str
    home_score: int | None
    away_score: int | None
    status: str
    winner_team_id: str | None
    played_at: datetime | None
    provider_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultSyncRun:
    id: int
    guild_id: str
    tournament_config_id: int
    provider: str
    status: str
    fetched_match_count: int
    applied_match_count: int
    warning_count: int
    details: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None


class ResultRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def save_provider_response_cache(
        self,
        *,
        provider: str,
        tournament_id: str,
        config_hash: str,
        fetched_match_count: int,
        request_metadata: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> int:
        async with self.pool.acquire() as connection:
            return await connection.fetchval(
                """
                insert into provider_response_cache (
                    provider,
                    tournament_id,
                    config_hash,
                    fetched_match_count,
                    request_metadata,
                    response_payload
                )
                values ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
                returning id
                """,
                provider,
                tournament_id,
                config_hash,
                fetched_match_count,
                json.dumps(request_metadata, sort_keys=True),
                json.dumps(response_payload, sort_keys=True),
            )

    async def record_delay_warning_once(
        self,
        *,
        provider: str,
        config_hash: str,
        provider_match_id: str,
        details: dict[str, Any],
    ) -> bool:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                insert into result_sync_warnings (
                    provider,
                    config_hash,
                    provider_match_id,
                    warning_type,
                    details
                )
                values ($1, $2, $3, 'provider_result_delay', $4::jsonb)
                on conflict (
                    provider,
                    config_hash,
                    provider_match_id,
                    warning_type
                ) do nothing
                returning id
                """,
                provider,
                config_hash,
                provider_match_id,
                json.dumps(details, sort_keys=True),
            )
            if row is not None:
                return True
            await connection.execute(
                """
                update result_sync_warnings set
                    last_seen_at = now()
                where provider = $1
                    and config_hash = $2
                    and provider_match_id = $3
                    and warning_type = 'provider_result_delay'
                """,
                provider,
                config_hash,
                provider_match_id,
            )

        return False

    async def list_match_results(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[StoredMatchResult]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select
                    match_id,
                    provider,
                    provider_match_id,
                    stage,
                    round_name,
                    group_id,
                    home_team_id,
                    away_team_id,
                    home_score,
                    away_score,
                    status,
                    winner_team_id,
                    played_at,
                    provider_payload
                from match_results
                where guild_id = $1
                    and tournament_config_id = $2
                order by stage asc, match_id asc
                """,
                guild_id,
                tournament_config_id,
            )

        return [_row_to_match_result(row) for row in rows]

    async def upsert_match_results(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        results: Sequence[StoredMatchResult],
    ) -> int:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                for result in results:
                    await connection.execute(
                        """
                        insert into match_results (
                            guild_id,
                            tournament_config_id,
                            match_id,
                            provider,
                            provider_match_id,
                            stage,
                            round_name,
                            group_id,
                            home_team_id,
                            away_team_id,
                            home_score,
                            away_score,
                            status,
                            winner_team_id,
                            played_at,
                            provider_payload,
                            synced_at
                        )
                        values (
                            $1, $2, $3, $4, $5, $6, $7, $8,
                            $9, $10, $11, $12, $13, $14, $15,
                            $16::jsonb, now()
                        )
                        on conflict (guild_id, tournament_config_id, match_id)
                        do update set
                            provider = excluded.provider,
                            provider_match_id = excluded.provider_match_id,
                            stage = excluded.stage,
                            round_name = excluded.round_name,
                            group_id = excluded.group_id,
                            home_team_id = excluded.home_team_id,
                            away_team_id = excluded.away_team_id,
                            home_score = excluded.home_score,
                            away_score = excluded.away_score,
                            status = excluded.status,
                            winner_team_id = excluded.winner_team_id,
                            played_at = excluded.played_at,
                            provider_payload = excluded.provider_payload,
                            synced_at = now(),
                            updated_at = now()
                        """,
                        guild_id,
                        tournament_config_id,
                        result.match_id,
                        result.provider,
                        result.provider_match_id,
                        result.stage,
                        result.round_name,
                        result.group_id,
                        result.home_team_id,
                        result.away_team_id,
                        result.home_score,
                        result.away_score,
                        result.status,
                        result.winner_team_id,
                        result.played_at,
                        json.dumps(result.provider_payload, sort_keys=True),
                    )
        return len(results)

    async def start_sync_run(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        provider: str,
    ) -> int:
        async with self.pool.acquire() as connection:
            return await connection.fetchval(
                """
                insert into result_sync_runs (
                    guild_id,
                    tournament_config_id,
                    provider,
                    status
                )
                values ($1, $2, $3, 'running')
                returning id
                """,
                guild_id,
                tournament_config_id,
                provider,
            )

    async def finish_sync_run(
        self,
        *,
        sync_run_id: int,
        status: str,
        fetched_match_count: int,
        applied_match_count: int,
        warning_count: int,
        details: dict[str, Any],
    ) -> ResultSyncRun:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                update result_sync_runs set
                    status = $2,
                    fetched_match_count = $3,
                    applied_match_count = $4,
                    warning_count = $5,
                    details = $6::jsonb,
                    finished_at = now()
                where id = $1
                returning
                    id,
                    guild_id,
                    tournament_config_id,
                    provider,
                    status,
                    fetched_match_count,
                    applied_match_count,
                    warning_count,
                    details,
                    started_at,
                    finished_at
                """,
                sync_run_id,
                status,
                fetched_match_count,
                applied_match_count,
                warning_count,
                json.dumps(details, sort_keys=True),
            )

        return _row_to_sync_run(row)

    async def latest_sync_run(
        self,
        *,
        guild_id: str,
        tournament_config_id: int | None = None,
    ) -> ResultSyncRun | None:
        async with self.pool.acquire() as connection:
            if tournament_config_id is None:
                row = await connection.fetchrow(
                    """
                    select
                        id,
                        guild_id,
                        tournament_config_id,
                        provider,
                        status,
                        fetched_match_count,
                        applied_match_count,
                        warning_count,
                        details,
                        started_at,
                        finished_at
                    from result_sync_runs
                    where guild_id = $1
                    order by started_at desc
                    limit 1
                    """,
                    guild_id,
                )
                return _row_to_sync_run(row) if row else None

            row = await connection.fetchrow(
                """
                select
                    id,
                    guild_id,
                    tournament_config_id,
                    provider,
                    status,
                    fetched_match_count,
                    applied_match_count,
                    warning_count,
                    details,
                    started_at,
                    finished_at
                from result_sync_runs
                where guild_id = $1
                    and tournament_config_id = $2
                order by started_at desc
                limit 1
                """,
                guild_id,
                tournament_config_id,
            )

        return _row_to_sync_run(row) if row else None

    async def list_sync_runs(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        limit: int = 25,
    ) -> list[ResultSyncRun]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select
                    id,
                    guild_id,
                    tournament_config_id,
                    provider,
                    status,
                    fetched_match_count,
                    applied_match_count,
                    warning_count,
                    details,
                    started_at,
                    finished_at
                from result_sync_runs
                where guild_id = $1
                    and tournament_config_id = $2
                order by started_at desc
                limit $3
                """,
                guild_id,
                tournament_config_id,
                limit,
            )

        return [_row_to_sync_run(row) for row in rows]


@dataclass(frozen=True)
class PredictionScore:
    prediction_entry_id: int
    guild_id: str
    tournament_config_id: int
    user_id: str
    display_name: str
    total_points: int
    group_points: int
    knockout_points: int
    breakdown: dict[str, Any]
    scoring_version: str
    recalculated_at: datetime


class PredictionScoreRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def upsert_scores(self, scores: Sequence[PredictionScore]) -> int:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                for score in scores:
                    await connection.execute(
                        """
                        insert into prediction_scores (
                            prediction_entry_id,
                            guild_id,
                            tournament_config_id,
                            user_id,
                            display_name,
                            total_points,
                            group_points,
                            knockout_points,
                            breakdown,
                            scoring_version,
                            recalculated_at
                        )
                        values (
                            $1, $2, $3, $4, $5, $6, $7, $8,
                            $9::jsonb, $10, $11
                        )
                        on conflict (prediction_entry_id)
                        do update set
                            display_name = excluded.display_name,
                            total_points = excluded.total_points,
                            group_points = excluded.group_points,
                            knockout_points = excluded.knockout_points,
                            breakdown = excluded.breakdown,
                            scoring_version = excluded.scoring_version,
                            recalculated_at = excluded.recalculated_at
                        """,
                        score.prediction_entry_id,
                        score.guild_id,
                        score.tournament_config_id,
                        score.user_id,
                        score.display_name,
                        score.total_points,
                        score.group_points,
                        score.knockout_points,
                        json.dumps(score.breakdown, sort_keys=True),
                        score.scoring_version,
                        score.recalculated_at,
                    )
        return len(scores)

    async def get_user_score(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
        user_id: str,
    ) -> PredictionScore | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    prediction_entry_id,
                    guild_id,
                    tournament_config_id,
                    user_id,
                    display_name,
                    total_points,
                    group_points,
                    knockout_points,
                    breakdown,
                    scoring_version,
                    recalculated_at
                from prediction_scores
                where guild_id = $1
                    and tournament_config_id = $2
                    and user_id = $3
                """,
                guild_id,
                tournament_config_id,
                user_id,
            )

        return _row_to_prediction_score(row) if row else None

    async def list_scores(
        self,
        *,
        guild_id: str,
        tournament_config_id: int,
    ) -> list[PredictionScore]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select
                    prediction_entry_id,
                    guild_id,
                    tournament_config_id,
                    user_id,
                    display_name,
                    total_points,
                    group_points,
                    knockout_points,
                    breakdown,
                    scoring_version,
                    recalculated_at
                from prediction_scores
                where guild_id = $1
                    and tournament_config_id = $2
                order by total_points desc, recalculated_at asc, display_name asc
                """,
                guild_id,
                tournament_config_id,
            )

        return [_row_to_prediction_score(row) for row in rows]


def _row_to_match_result(row: Any) -> StoredMatchResult:
    return StoredMatchResult(
        match_id=row["match_id"],
        provider=row["provider"],
        provider_match_id=row["provider_match_id"],
        stage=row["stage"],
        round_name=row["round_name"],
        group_id=row["group_id"],
        home_team_id=row["home_team_id"],
        away_team_id=row["away_team_id"],
        home_score=row["home_score"],
        away_score=row["away_score"],
        status=row["status"],
        winner_team_id=row["winner_team_id"],
        played_at=row["played_at"],
        provider_payload=_json_dict(row["provider_payload"]),
    )


def _row_to_sync_run(row: Any) -> ResultSyncRun:
    return ResultSyncRun(
        id=row["id"],
        guild_id=row["guild_id"],
        tournament_config_id=row["tournament_config_id"],
        provider=row["provider"],
        status=row["status"],
        fetched_match_count=row["fetched_match_count"],
        applied_match_count=row["applied_match_count"],
        warning_count=row["warning_count"],
        details=_json_dict(row["details"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _row_to_prediction_score(row: Any) -> PredictionScore:
    return PredictionScore(
        prediction_entry_id=row["prediction_entry_id"],
        guild_id=row["guild_id"],
        tournament_config_id=row["tournament_config_id"],
        user_id=row["user_id"],
        display_name=row["display_name"],
        total_points=row["total_points"],
        group_points=row["group_points"],
        knockout_points=row["knockout_points"],
        breakdown=_json_dict(row["breakdown"]),
        scoring_version=row["scoring_version"],
        recalculated_at=row["recalculated_at"],
    )


def _row_to_tie_breaker_adjudication(row: Any) -> StoredTieBreakerAdjudication:
    return StoredTieBreakerAdjudication(
        id=row["id"],
        tournament_id=row["tournament_id"],
        config_hash=row["config_hash"],
        scope=row["scope"],
        scope_key=row["scope_key"],
        team_ids=tuple(_json_list(row["team_ids"])),
        ordered_team_ids=tuple(_json_list(row["ordered_team_ids"])),
        criterion=row["criterion"],
        reason=row["reason"],
        actor_user_id=row["actor_user_id"],
        created_at=row["created_at"],
    )


def _tie_breaker_audit_snapshot(row: Any) -> dict[str, object]:
    return {
        "id": row["id"],
        "team_ids": _json_list(row["team_ids"]),
        "ordered_team_ids": _json_list(row["ordered_team_ids"]),
        "criterion": row["criterion"],
        "reason": row["reason"],
        "actor_user_id": row["actor_user_id"],
        "created_at": row["created_at"].isoformat(),
    }


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        decoded = json.loads(value)
        return dict(decoded) if isinstance(decoded, dict) else {}
    return dict(value) if value else {}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        decoded = json.loads(value)
        return [str(item) for item in decoded] if isinstance(decoded, list) else []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []
