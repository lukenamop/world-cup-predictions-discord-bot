from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
