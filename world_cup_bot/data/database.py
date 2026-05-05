from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from world_cup_bot.data.migrations import apply_migrations, discover_migrations


DEFAULT_MIGRATIONS_PATH = Path(__file__).with_name("migrations")


class Database:
    def __init__(self, database_url: str, *, migrations_path: Path | None = None) -> None:
        self.database_url = database_url
        self.migrations_path = migrations_path or DEFAULT_MIGRATIONS_PATH
        self.pool: Any | None = None

    async def connect(self) -> None:
        import asyncpg

        self.pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=1,
            max_size=5,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def apply_migrations(self) -> list[str]:
        if self.pool is None:
            raise RuntimeError("Database pool is not connected.")

        migrations = discover_migrations(self.migrations_path)
        async with self.pool.acquire() as connection:
            return await apply_migrations(connection, migrations)

    async def health_check(self) -> str:
        if self.pool is None:
            raise RuntimeError("Database pool is not connected.")

        async with self.pool.acquire() as connection:
            return await connection.fetchval("select 'ok'")

    async def record_startup(self, *, bot_env: str) -> None:
        await self._record_health(
            bot_env=bot_env,
            last_started_at=datetime.now(timezone.utc),
            last_ready_at=None,
            guild_count=None,
            command_sync_at=None,
        )

    async def record_ready(
        self,
        *,
        bot_env: str,
        guild_count: int,
        command_sync_at: datetime | None,
    ) -> None:
        await self._record_health(
            bot_env=bot_env,
            last_started_at=None,
            last_ready_at=datetime.now(timezone.utc),
            guild_count=guild_count,
            command_sync_at=command_sync_at,
        )

    async def _record_health(
        self,
        *,
        bot_env: str,
        last_started_at: datetime | None,
        last_ready_at: datetime | None,
        guild_count: int | None,
        command_sync_at: datetime | None,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Database pool is not connected.")

        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                insert into bot_health (
                    id,
                    bot_env,
                    last_started_at,
                    last_ready_at,
                    last_guild_count,
                    last_command_sync_at
                )
                values (true, $1, $2, $3, $4, $5)
                on conflict (id) do update set
                    bot_env = excluded.bot_env,
                    last_started_at = coalesce(
                        excluded.last_started_at,
                        bot_health.last_started_at
                    ),
                    last_ready_at = coalesce(
                        excluded.last_ready_at,
                        bot_health.last_ready_at
                    ),
                    last_guild_count = coalesce(
                        excluded.last_guild_count,
                        bot_health.last_guild_count
                    ),
                    last_command_sync_at = coalesce(
                        excluded.last_command_sync_at,
                        bot_health.last_command_sync_at
                    )
                """,
                bot_env,
                last_started_at,
                last_ready_at,
                guild_count,
                command_sync_at,
            )
