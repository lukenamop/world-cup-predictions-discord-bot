from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Mapping, Sequence

from world_cup_bot.data.repositories import (
    ActiveTournamentConfig,
    GuildActiveTournamentConfig,
    ResultRepository,
    ResultSyncRun,
    StoredMatchResult,
    TournamentConfigRepository,
)
from world_cup_bot.domain.predictions import (
    ROUND_ORDER,
    PredictionValidationError,
    TournamentModel,
    get_round_matches,
)
from world_cup_bot.domain.scoring import actual_tournament_data
from world_cup_bot.domain.standings import FINISHED_STATUSES, MatchResult
from world_cup_bot.services.live_results_client import (
    LiveMatchResult,
    LiveResultsClient,
    LiveResultsError,
    create_live_results_client,
)


LOGGER = logging.getLogger(__name__)
RESULT_DELAY_ALLOWANCE = timedelta(hours=6)


class ResultSyncServiceError(RuntimeError):
    """Raised when result sync cannot run for a guild."""


@dataclass(frozen=True)
class ResultSyncSummary:
    sync_run: ResultSyncRun
    fetched_match_count: int
    applied_match_count: int
    skipped_match_count: int
    warning_count: int


@dataclass(frozen=True)
class LiveResultsFetch:
    provider_name: str
    tournament_id: str
    config_hash: str
    live_results: list[LiveMatchResult]
    provider_response_cache_id: int | None


class ResultSyncService:
    def __init__(
        self,
        pool: Any,
        *,
        provider_name: str,
        client: LiveResultsClient | None = None,
    ) -> None:
        self.tournaments = TournamentConfigRepository(pool)
        self.results = ResultRepository(pool)
        self.provider_name = provider_name
        self.client = client

    async def sync_guild(self, *, guild_id: str) -> ResultSyncSummary:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            raise ResultSyncServiceError("Run `/admin setup` to attach tournament data first.")

        fetched = await self.fetch_matches(tournament=tournament)
        return await self.sync_guild_from_fetch(
            guild_id=guild_id,
            tournament=tournament,
            fetched=fetched,
        )

    async def fetch_matches(
        self,
        *,
        tournament: ActiveTournamentConfig | GuildActiveTournamentConfig,
    ) -> LiveResultsFetch:
        client = self._client()
        try:
            live_results = await client.fetch_matches(tournament.config)
        except LiveResultsError as exc:
            raise ResultSyncServiceError(str(exc)) from exc

        cache_id = await self.results.save_provider_response_cache(
            provider=client.provider_name,
            tournament_id=tournament.tournament_id,
            config_hash=tournament.config_hash,
            fetched_match_count=len(live_results),
            request_metadata=_provider_request_metadata(tournament.config),
            response_payload={
                "matches": [result.payload for result in live_results],
            },
        )
        return LiveResultsFetch(
            provider_name=client.provider_name,
            tournament_id=tournament.tournament_id,
            config_hash=tournament.config_hash,
            live_results=live_results,
            provider_response_cache_id=cache_id,
        )

    async def sync_guild_from_fetch(
        self,
        *,
        guild_id: str,
        tournament: ActiveTournamentConfig | GuildActiveTournamentConfig,
        fetched: LiveResultsFetch,
    ) -> ResultSyncSummary:
        sync_run_id = await self.results.start_sync_run(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
            provider=fetched.provider_name,
        )

        try:
            stored, skipped = _map_live_results(
                provider_name=fetched.provider_name,
                tournament_config=tournament.config,
                live_results=fetched.live_results,
            )
            delay_warnings = await self._record_delay_warnings(
                provider_name=fetched.provider_name,
                config_hash=tournament.config_hash,
                tournament_config=tournament.config,
                live_results=fetched.live_results,
            )
            applied = await self.results.upsert_match_results(
                guild_id=guild_id,
                tournament_config_id=tournament.id,
                results=stored,
            )
            sync_run = await self.results.finish_sync_run(
                sync_run_id=sync_run_id,
                status="succeeded",
                fetched_match_count=len(fetched.live_results),
                applied_match_count=applied,
                warning_count=len(skipped) + len(delay_warnings),
                details={
                    "provider_response_cache_id": fetched.provider_response_cache_id,
                    "skipped_provider_match_ids": skipped[:25],
                    "delayed_provider_match_ids": delay_warnings[:25],
                },
            )
        except Exception as exc:
            sync_run = await self.results.finish_sync_run(
                sync_run_id=sync_run_id,
                status="failed",
                fetched_match_count=len(fetched.live_results),
                applied_match_count=0,
                warning_count=1,
                details={
                    "provider_response_cache_id": fetched.provider_response_cache_id,
                    "error": str(exc),
                },
            )
            raise ResultSyncServiceError(str(exc)) from exc

        return ResultSyncSummary(
            sync_run=sync_run,
            fetched_match_count=sync_run.fetched_match_count,
            applied_match_count=sync_run.applied_match_count,
            skipped_match_count=len(skipped),
            warning_count=sync_run.warning_count,
        )

    async def record_fetch_failure(
        self,
        *,
        tournament: GuildActiveTournamentConfig,
        provider_name: str,
        error: str,
    ) -> ResultSyncRun:
        sync_run_id = await self.results.start_sync_run(
            guild_id=tournament.guild_id,
            tournament_config_id=tournament.id,
            provider=provider_name,
        )
        return await self.results.finish_sync_run(
            sync_run_id=sync_run_id,
            status="failed",
            fetched_match_count=0,
            applied_match_count=0,
            warning_count=1,
            details={"error": error},
        )

    async def latest_sync_run(self, *, guild_id: str) -> ResultSyncRun | None:
        tournament = await self.tournaments.get_active_config(guild_id)
        if tournament is None:
            return None
        return await self.results.latest_sync_run(
            guild_id=guild_id,
            tournament_config_id=tournament.id,
        )

    async def _record_delay_warnings(
        self,
        *,
        provider_name: str,
        config_hash: str,
        tournament_config: Mapping[str, Any],
        live_results: Sequence[LiveMatchResult],
    ) -> list[str]:
        delayed = _delayed_provider_match_ids(
            tournament_config=tournament_config,
            live_results=live_results,
        )
        newly_logged: list[str] = []
        for details in delayed:
            inserted = await self.results.record_delay_warning_once(
                provider=provider_name,
                config_hash=config_hash,
                provider_match_id=details["provider_match_id"],
                details=details,
            )
            if inserted:
                LOGGER.warning(
                    "Live result provider delay detected; provider=%s match_id=%s provider_match_id=%s kickoff_utc=%s status=%s",
                    provider_name,
                    details["match_id"],
                    details["provider_match_id"],
                    details["kickoff_utc"],
                    details["status"],
                )
                newly_logged.append(details["provider_match_id"])
        return newly_logged

    def _client(self) -> LiveResultsClient:
        try:
            return self.client or create_live_results_client(
                provider_name=self.provider_name,
            )
        except LiveResultsError as exc:
            raise ResultSyncServiceError(str(exc)) from exc


def _map_live_results(
    *,
    provider_name: str,
    tournament_config: Mapping[str, Any],
    live_results: list[LiveMatchResult],
) -> tuple[list[StoredMatchResult], list[str]]:
    live_by_provider_id = {
        live_result.provider_match_id: live_result
        for live_result in live_results
        if live_result.provider_match_id
    }
    group_fixture_lookup = _fixture_lookup(tournament_config.get("fixtures"))
    knockout_fixture_lookup = _fixture_lookup(tournament_config.get("knockout_fixtures"))
    matched_provider_ids: set[str] = set()
    stored: list[StoredMatchResult] = []
    skipped: list[str] = []

    for provider_match_id, fixture in group_fixture_lookup.items():
        live_result = live_by_provider_id.get(provider_match_id)
        if live_result is None:
            continue
        matched_provider_ids.add(provider_match_id)
        stored.append(
            _stored_group_result(
                provider_name=provider_name,
                fixture=fixture,
                live_result=live_result,
            )
        )

    if knockout_fixture_lookup:
        model = TournamentModel.from_config(tournament_config)
        actual_data = actual_tournament_data(
            model,
            [_to_domain_result(result) for result in stored],
        )
        knockout = actual_data.setdefault("knockout", {})
        for round_name in ROUND_ORDER:
            try:
                matches = get_round_matches(model, actual_data, round_name)
            except PredictionValidationError:
                matches = ()
            match_by_id = {match.id: match for match in matches}
            for provider_match_id, fixture in knockout_fixture_lookup.items():
                if fixture.get("round_name") != round_name:
                    continue
                live_result = live_by_provider_id.get(provider_match_id)
                if live_result is None:
                    continue
                match = match_by_id.get(str(fixture["id"]))
                if match is None:
                    skipped.append(provider_match_id)
                    continue
                matched_provider_ids.add(provider_match_id)
                winner_team_id = _winner_team_id(
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                    status=live_result.status,
                    home_score=live_result.home_score,
                    away_score=live_result.away_score,
                    winner_side=live_result.winner_side,
                )
                stored.append(
                    StoredMatchResult(
                        match_id=match.id,
                        provider=provider_name,
                        provider_match_id=live_result.provider_match_id,
                        stage="knockout",
                        round_name=round_name,
                        group_id=None,
                        home_team_id=match.home_team_id,
                        away_team_id=match.away_team_id,
                        home_score=live_result.home_score,
                        away_score=live_result.away_score,
                        status=live_result.status,
                        winner_team_id=winner_team_id,
                        played_at=live_result.played_at or _fixture_kickoff(fixture),
                        provider_payload=live_result.payload,
                    )
                )
                if winner_team_id:
                    knockout.setdefault(round_name, []).append(
                        {
                            "match_id": match.id,
                            "home_team_id": match.home_team_id,
                            "away_team_id": match.away_team_id,
                            "winner_team_id": winner_team_id,
                        }
                    )

    known_provider_ids = set(group_fixture_lookup) | set(knockout_fixture_lookup)
    skipped.extend(
        live_result.provider_match_id
        for live_result in live_results
        if (
            live_result.provider_match_id
            and live_result.provider_match_id not in known_provider_ids
            and live_result.provider_match_id not in matched_provider_ids
        )
    )
    return stored, skipped


def _stored_group_result(
    *,
    provider_name: str,
    fixture: Mapping[str, Any],
    live_result: LiveMatchResult,
) -> StoredMatchResult:
    home_team_id = str(fixture["home_team_id"])
    away_team_id = str(fixture["away_team_id"])
    return StoredMatchResult(
        match_id=str(fixture["id"]),
        provider=provider_name,
        provider_match_id=live_result.provider_match_id,
        stage=str(fixture.get("stage") or "group"),
        round_name=fixture.get("round_name") if isinstance(fixture.get("round_name"), str) else None,
        group_id=fixture.get("group_id") if isinstance(fixture.get("group_id"), str) else None,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_score=live_result.home_score,
        away_score=live_result.away_score,
        status=live_result.status,
        winner_team_id=_winner_team_id(
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            status=live_result.status,
            home_score=live_result.home_score,
            away_score=live_result.away_score,
            winner_side=live_result.winner_side,
        ),
        played_at=live_result.played_at or _fixture_kickoff(fixture),
        provider_payload=live_result.payload,
    )


def _to_domain_result(result: StoredMatchResult) -> MatchResult:
    return MatchResult(
        match_id=result.match_id,
        stage=result.stage,
        home_team_id=result.home_team_id,
        away_team_id=result.away_team_id,
        status=result.status,
        home_score=result.home_score,
        away_score=result.away_score,
        group_id=result.group_id,
        round_name=result.round_name,
        winner_team_id=result.winner_team_id,
        played_at=result.played_at,
    )


def _fixture_lookup(raw_fixtures: object) -> dict[str, Mapping[str, Any]]:
    if not isinstance(raw_fixtures, list):
        return {}
    lookup: dict[str, Mapping[str, Any]] = {}
    for fixture in raw_fixtures:
        if not isinstance(fixture, Mapping):
            continue
        fixture_id = fixture.get("id")
        provider_match_id = fixture.get("provider_match_id")
        if isinstance(fixture_id, str):
            lookup[fixture_id] = fixture
        if isinstance(provider_match_id, str):
            lookup[provider_match_id] = fixture
    return lookup


def _provider_request_metadata(tournament_config: Mapping[str, Any]) -> dict[str, Any]:
    tournament = tournament_config.get("tournament")
    if not isinstance(tournament, Mapping):
        return {}
    metadata = tournament.get("source_metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    tournament_data = metadata.get("tournament_data")
    if not isinstance(tournament_data, Mapping):
        tournament_data = {}
    return {
        "tournament_id": tournament.get("id"),
        "start_date": tournament.get("start_date"),
        "end_date": tournament.get("end_date"),
        "competition_id": tournament_data.get("competition_id"),
    }


def _delayed_provider_match_ids(
    *,
    tournament_config: Mapping[str, Any],
    live_results: Sequence[LiveMatchResult],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    checked_at = now or datetime.now(timezone.utc)
    live_by_provider_id = {
        live_result.provider_match_id: live_result
        for live_result in live_results
        if live_result.provider_match_id
    }
    delayed: list[dict[str, Any]] = []
    for fixture in _all_fixtures(tournament_config):
        provider_match_id = fixture.get("provider_match_id")
        if not isinstance(provider_match_id, str) or not provider_match_id:
            continue
        kickoff = _fixture_kickoff(fixture)
        if kickoff is None or checked_at <= kickoff + RESULT_DELAY_ALLOWANCE:
            continue
        live_result = live_by_provider_id.get(provider_match_id)
        status = live_result.status if live_result else "MISSING"
        if status in FINISHED_STATUSES:
            continue
        delayed.append(
            {
                "match_id": str(fixture.get("id") or ""),
                "provider_match_id": provider_match_id,
                "kickoff_utc": kickoff.isoformat(),
                "status": status,
                "checked_at": checked_at.isoformat(),
            }
        )
    return delayed


def _all_fixtures(tournament_config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    fixtures: list[Mapping[str, Any]] = []
    for key in ("fixtures", "knockout_fixtures"):
        raw = tournament_config.get(key)
        if not isinstance(raw, list):
            continue
        fixtures.extend(fixture for fixture in raw if isinstance(fixture, Mapping))
    return fixtures


def _winner_team_id(
    *,
    home_team_id: str,
    away_team_id: str,
    status: str,
    home_score: int | None,
    away_score: int | None,
    winner_side: str | None = None,
) -> str | None:
    if status not in FINISHED_STATUSES:
        return None
    if winner_side == "HOME_TEAM":
        return home_team_id
    if winner_side == "AWAY_TEAM":
        return away_team_id
    if home_score is None or away_score is None or home_score == away_score:
        return None
    return home_team_id if home_score > away_score else away_team_id


def _fixture_kickoff(fixture: Mapping[str, Any]) -> datetime | None:
    kickoff = fixture.get("kickoff_utc")
    if not isinstance(kickoff, str):
        return None
    normalized = kickoff.removesuffix("Z") + "+00:00" if kickoff.endswith("Z") else kickoff
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)
