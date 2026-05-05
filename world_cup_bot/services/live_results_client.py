from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class LiveResultsError(RuntimeError):
    """Raised when a live results provider cannot be reached or parsed."""


@dataclass(frozen=True)
class LiveMatchResult:
    provider_match_id: str
    status: str
    home_score: int | None
    away_score: int | None
    played_at: datetime | None
    winner_side: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class LiveResultsClient(Protocol):
    provider_name: str

    async def fetch_matches(self, tournament_config: Mapping[str, Any]) -> list[LiveMatchResult]:
        ...


class FootballDataOrgClient:
    provider_name = "football_data_org"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.football-data.org/v4",
        competition_code: str = "WC",
        timeout_seconds: int = 20,
        max_attempts: int = 3,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.competition_code = competition_code
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)

    async def fetch_matches(self, tournament_config: Mapping[str, Any]) -> list[LiveMatchResult]:
        if not self.api_key:
            raise LiveResultsError("LIVE_RESULTS_API_KEY is required for football-data.org sync.")

        season = _tournament_start_year(tournament_config)
        query = urlencode({"season": season}) if season else ""
        url = f"{self.base_url}/competitions/{self.competition_code}/matches"
        if query:
            url = f"{url}?{query}"
        return await asyncio.to_thread(self._fetch_matches_sync, url)

    def _fetch_matches_sync(self, url: str) -> list[LiveMatchResult]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Auth-Token": self.api_key,
            },
        )
        payload: Any = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if attempt >= self.max_attempts or exc.code not in {429, 500, 502, 503, 504}:
                    raise LiveResultsError(f"football-data.org returned HTTP {exc.code}.") from exc
                _sleep_for_backoff(exc, attempt)
            except (URLError, TimeoutError) as exc:
                if attempt >= self.max_attempts:
                    raise LiveResultsError("football-data.org sync request failed.") from exc
                _sleep_for_backoff(None, attempt)
            except json.JSONDecodeError as exc:
                raise LiveResultsError("football-data.org returned invalid JSON.") from exc

        matches = payload.get("matches")
        if not isinstance(matches, list):
            raise LiveResultsError("football-data.org response did not include matches.")

        return [_parse_football_data_match(match) for match in matches if isinstance(match, Mapping)]


def create_live_results_client(
    *,
    provider_name: str,
    api_key: str | None,
) -> LiveResultsClient:
    if provider_name == FootballDataOrgClient.provider_name:
        return FootballDataOrgClient(api_key=api_key or "")
    raise LiveResultsError(f"Unsupported live results provider: {provider_name}")


def _parse_football_data_match(raw_match: Mapping[str, Any]) -> LiveMatchResult:
    score = raw_match.get("score") if isinstance(raw_match.get("score"), Mapping) else {}
    full_time = score.get("fullTime") if isinstance(score.get("fullTime"), Mapping) else {}
    return LiveMatchResult(
        provider_match_id=str(raw_match.get("id") or ""),
        status=str(raw_match.get("status") or "UNKNOWN"),
        home_score=_optional_int(full_time.get("home")),
        away_score=_optional_int(full_time.get("away")),
        played_at=_parse_utc_datetime(raw_match.get("utcDate")),
        winner_side=_winner_side(score.get("winner")),
        payload=dict(raw_match),
    )


def _tournament_start_year(config: Mapping[str, Any]) -> str | None:
    tournament = config.get("tournament")
    if not isinstance(tournament, Mapping):
        return None
    start_date = tournament.get("start_date")
    if not isinstance(start_date, str) or len(start_date) < 4:
        return None
    return start_date[:4]


def _parse_utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _winner_side(value: object) -> str | None:
    return value if value in {"HOME_TEAM", "AWAY_TEAM"} else None


def _sleep_for_backoff(error: HTTPError | None, attempt: int) -> None:
    import time

    reset_after = None
    if error is not None:
        reset_header = error.headers.get("X-RequestCounter-Reset")
        try:
            reset_after = int(reset_header) if reset_header else None
        except ValueError:
            reset_after = None
    time.sleep(min(reset_after or (2 ** (attempt - 1)), 30))
