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


class FifaPublicCalendarClient:
    provider_name = "fifa_public_calendar"

    def __init__(
        self,
        *,
        user_agent: str,
        base_url: str = "https://api.fifa.com/api/v3",
        language: str = "en",
        match_count: int = 500,
        timeout_seconds: int = 20,
        max_attempts: int = 3,
    ) -> None:
        cleaned_user_agent = user_agent.strip()
        if not cleaned_user_agent:
            raise LiveResultsError("USER_AGENT is required for FIFA calendar requests.")
        self.user_agent = cleaned_user_agent
        self.base_url = base_url.rstrip("/")
        self.language = language
        self.match_count = match_count
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)

    async def fetch_matches(self, tournament_config: Mapping[str, Any]) -> list[LiveMatchResult]:
        url = self._matches_url(tournament_config)
        return await asyncio.to_thread(self._fetch_matches_sync, url)

    def _matches_url(self, tournament_config: Mapping[str, Any]) -> str:
        tournament = _mapping(tournament_config.get("tournament"))
        metadata = _mapping(tournament.get("source_metadata"))
        tournament_data = _mapping(metadata.get("tournament_data"))
        competition_id = str(tournament_data.get("competition_id") or "17")
        query = urlencode(
            {
                "language": self.language,
                "from": _tournament_date_boundary(
                    tournament_config,
                    "start_date",
                    start=True,
                ),
                "to": _tournament_date_boundary(
                    tournament_config,
                    "end_date",
                    start=False,
                ),
                "count": self.match_count,
                "idCompetition": competition_id,
            }
        )
        return f"{self.base_url}/calendar/matches?{query}"

    def _fetch_matches_sync(self, url: str) -> list[LiveMatchResult]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
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
                    raise LiveResultsError(f"FIFA calendar returned HTTP {exc.code}.") from exc
                _sleep_for_backoff(exc, attempt)
            except (URLError, TimeoutError) as exc:
                if attempt >= self.max_attempts:
                    raise LiveResultsError("FIFA calendar sync request failed.") from exc
                _sleep_for_backoff(None, attempt)
            except json.JSONDecodeError as exc:
                raise LiveResultsError("FIFA calendar returned invalid JSON.") from exc

        matches = payload.get("Results")
        if not isinstance(matches, list):
            raise LiveResultsError("FIFA calendar response did not include matches.")

        return [_parse_fifa_match(match) for match in matches if isinstance(match, Mapping)]


def create_live_results_client(
    *,
    provider_name: str,
    user_agent: str,
) -> LiveResultsClient:
    if provider_name == FifaPublicCalendarClient.provider_name:
        return FifaPublicCalendarClient(user_agent=user_agent)
    raise LiveResultsError(f"Unsupported live results provider: {provider_name}")


def _parse_fifa_match(raw_match: Mapping[str, Any]) -> LiveMatchResult:
    home = _mapping(raw_match.get("Home"))
    away = _mapping(raw_match.get("Away"))
    home_score = _optional_int(raw_match.get("HomeTeamScore"))
    away_score = _optional_int(raw_match.get("AwayTeamScore"))
    return LiveMatchResult(
        provider_match_id=str(raw_match.get("IdMatch") or ""),
        status=_fifa_status(raw_match),
        home_score=home_score if home_score is not None else _optional_int(home.get("Score")),
        away_score=away_score if away_score is not None else _optional_int(away.get("Score")),
        played_at=_parse_utc_datetime(raw_match.get("Date")),
        winner_side=_fifa_winner_side(
            raw_match.get("Winner"),
            home.get("IdTeam"),
            away.get("IdTeam"),
        ),
        payload=dict(raw_match),
    )


def _tournament_date_boundary(
    tournament_config: Mapping[str, Any],
    key: str,
    *,
    start: bool,
) -> str:
    tournament = _mapping(tournament_config.get("tournament"))
    value = tournament.get(key)
    if value is not None:
        if not isinstance(value, str) or len(value) < 10:
            raise LiveResultsError(f"FIFA sync requires tournament.{key}.")
        return _format_date_boundary(value[:10], start=start)

    kickoff_dates = _fixture_kickoff_dates(tournament_config)
    if not kickoff_dates:
        raise LiveResultsError(
            f"FIFA sync requires tournament.{key} or fixture kickoff_utc values."
        )
    date_value = min(kickoff_dates) if start else max(kickoff_dates)
    return _format_date_boundary(date_value, start=start)


def _format_date_boundary(date_value: str, *, start: bool) -> str:
    suffix = "T00:00:00Z" if start else "T23:59:59Z"
    return f"{date_value}{suffix}"


def _fixture_kickoff_dates(tournament_config: Mapping[str, Any]) -> list[str]:
    dates: list[str] = []
    for fixture_group in ("fixtures", "knockout_fixtures"):
        raw_fixtures = tournament_config.get(fixture_group)
        if not isinstance(raw_fixtures, list):
            continue
        for fixture in raw_fixtures:
            if not isinstance(fixture, Mapping):
                continue
            kickoff = fixture.get("kickoff_utc")
            parsed = _parse_utc_datetime(kickoff)
            if parsed is not None:
                dates.append(parsed.date().isoformat())
    return dates


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


def _fifa_status(raw_match: Mapping[str, Any]) -> str:
    match_status = _optional_int(raw_match.get("MatchStatus"))
    if match_status == 0:
        return "FINISHED"
    if match_status == 1:
        return "SCHEDULED"
    if any(raw_match.get(key) for key in ("MatchTime", "FirstHalfTime", "SecondHalfTime")):
        return "IN_PLAY"
    return "UNKNOWN"


def _fifa_winner_side(
    winner_team_id: object,
    home_fifa_team_id: object,
    away_fifa_team_id: object,
) -> str | None:
    winner = str(winner_team_id) if winner_team_id else ""
    if winner and winner == str(home_fifa_team_id):
        return "HOME_TEAM"
    if winner and winner == str(away_fifa_team_id):
        return "AWAY_TEAM"
    return None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


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
