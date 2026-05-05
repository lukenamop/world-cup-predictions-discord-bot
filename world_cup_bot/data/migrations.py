from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


MIGRATION_NAME_PATTERN = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")


class AsyncMigrationConnection(Protocol):
    async def execute(self, query: str, *args: object) -> object:
        ...

    async def fetch(self, query: str, *args: object) -> list[object]:
        ...

    def transaction(self) -> object:
        ...


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str


def discover_migrations(migrations_path: Path) -> list[Migration]:
    if not migrations_path.exists():
        return []

    migrations: list[Migration] = []
    seen_numbers: set[str] = set()
    for path in sorted(migrations_path.glob("*.sql")):
        if not MIGRATION_NAME_PATTERN.match(path.name):
            raise ValueError(
                f"Migration {path.name} must match 001_descriptive_name.sql"
            )

        number = path.name.split("_", 1)[0]
        if number in seen_numbers:
            raise ValueError(f"Duplicate migration number: {number}")
        seen_numbers.add(number)
        migrations.append(Migration(name=path.name, sql=path.read_text()))

    return migrations


async def apply_migrations(
    connection: AsyncMigrationConnection,
    migrations: Iterable[Migration],
) -> list[str]:
    await connection.execute(
        """
        create table if not exists schema_migrations (
            name text primary key,
            applied_at timestamptz not null default now()
        )
        """
    )

    rows = await connection.fetch("select name from schema_migrations")
    applied = {row["name"] for row in rows}
    applied_now: list[str] = []

    for migration in migrations:
        if migration.name in applied:
            continue

        async with connection.transaction():
            await connection.execute(migration.sql)
            await connection.execute(
                "insert into schema_migrations (name) values ($1)",
                migration.name,
            )
        applied_now.append(migration.name)

    return applied_now
