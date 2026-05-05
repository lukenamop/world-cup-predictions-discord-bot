from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def effective_lock_deadline(
    *,
    configured_deadline_utc: datetime | None,
    tournament_config: Mapping[str, Any],
) -> datetime | None:
    if configured_deadline_utc is not None:
        return _as_utc(configured_deadline_utc)

    kickoff_times = [
        parsed
        for parsed in (
            _parse_utc_datetime(fixture.get("kickoff_utc"))
            for fixture in _fixtures(tournament_config)
        )
        if parsed is not None
    ]
    return min(kickoff_times) if kickoff_times else None


def is_prediction_locked(
    *,
    configured_deadline_utc: datetime | None,
    tournament_config: Mapping[str, Any],
    now_utc: datetime | None = None,
) -> bool:
    deadline = effective_lock_deadline(
        configured_deadline_utc=configured_deadline_utc,
        tournament_config=tournament_config,
    )
    if deadline is None:
        return False

    now = _as_utc(now_utc or datetime.now(timezone.utc))
    return now >= deadline


def _fixtures(tournament_config: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    fixtures = tournament_config.get("fixtures")
    if not isinstance(fixtures, list):
        return []
    return [fixture for fixture in fixtures if isinstance(fixture, Mapping)]


def _parse_utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
