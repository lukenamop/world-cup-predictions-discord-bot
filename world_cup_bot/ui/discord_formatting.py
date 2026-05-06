from __future__ import annotations

from datetime import datetime, timezone


DISCORD_TIMESTAMP_STYLES = frozenset({"t", "T", "d", "D", "f", "F", "R"})


def discord_timestamp(value: datetime, style: str = "f") -> str:
    if style not in DISCORD_TIMESTAMP_STYLES:
        raise ValueError(f"Unsupported Discord timestamp style: {style}")
    return f"<t:{_unix_timestamp(value)}:{style}>"


def discord_datetime(value: datetime) -> str:
    return f"{discord_timestamp(value, 'F')} ({discord_timestamp(value, 'R')})"


def _unix_timestamp(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())
