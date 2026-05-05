from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from world_cup_bot.data.repositories import (
    GuildActiveTournamentConfig,
    TournamentConfigRepository,
)
from world_cup_bot.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardServiceError,
)
from world_cup_bot.services.result_sync_service import (
    ResultSyncSummary,
    ResultSyncService,
    ResultSyncServiceError,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultSyncFailure:
    guild_id: str
    config_hash: str
    error: str


@dataclass(frozen=True)
class ResultSyncJobReport:
    summaries: list[ResultSyncSummary]
    failures: list[ResultSyncFailure]
    fetched_match_count: int


async def sync_all_active_guilds(bot: Any) -> ResultSyncJobReport:
    tournaments = await TournamentConfigRepository(
        bot.database.pool
    ).list_active_configs()
    summaries: list[ResultSyncSummary] = []
    failures: list[ResultSyncFailure] = []
    fetched_match_count = 0
    grouped = _group_by_config_hash(tournaments)
    if len(grouped) > 1:
        LOGGER.warning(
            "Multiple active tournament config hashes found during sync; group_count=%s",
            len(grouped),
        )
    for config_hash, tournament_group in grouped.items():
        sync_service = ResultSyncService(
            bot.database.pool,
            provider_name=bot.settings.live_results_provider,
        )
        try:
            fetched = await sync_service.fetch_matches(tournament=tournament_group[0])
        except ResultSyncServiceError as exc:
            LOGGER.exception(
                "Result sync provider fetch failed; provider=%s config_hash=%s guild_count=%s",
                bot.settings.live_results_provider,
                config_hash[:12],
                len(tournament_group),
            )
            for tournament in tournament_group:
                await sync_service.record_fetch_failure(
                    tournament=tournament,
                    provider_name=bot.settings.live_results_provider,
                    error=str(exc),
                )
                failures.append(
                    ResultSyncFailure(
                        guild_id=tournament.guild_id,
                        config_hash=tournament.config_hash,
                        error=str(exc),
                    )
                )
            continue

        LOGGER.info(
            "Result sync provider fetch complete; provider=%s config_hash=%s fetched=%s guild_count=%s",
            fetched.provider_name,
            config_hash[:12],
            len(fetched.live_results),
            len(tournament_group),
        )
        fetched_match_count += len(fetched.live_results)

        for tournament in tournament_group:
            guild_id = tournament.guild_id
            try:
                summary = await sync_service.sync_guild_from_fetch(
                    guild_id=guild_id,
                    tournament=tournament,
                    fetched=fetched,
                )
                recalculation = await LeaderboardService(bot.database.pool).recalculate(
                    guild_id=guild_id
                )
            except (ResultSyncServiceError, LeaderboardServiceError) as exc:
                LOGGER.exception("Result sync job failed for guild_id=%s", guild_id)
                failures.append(
                    ResultSyncFailure(
                        guild_id=guild_id,
                        config_hash=tournament.config_hash,
                        error=str(exc),
                    )
                )
                continue

            summaries.append(summary)
            LOGGER.info(
                "Result sync job complete; guild_id=%s fetched=%s applied=%s scored=%s scoring_version=%s",
                guild_id,
                summary.fetched_match_count,
                summary.applied_match_count,
                recalculation.scored_prediction_count,
                recalculation.scoring_version,
            )
    return ResultSyncJobReport(
        summaries=summaries,
        failures=failures,
        fetched_match_count=fetched_match_count,
    )


def _group_by_config_hash(
    tournaments: list[GuildActiveTournamentConfig],
) -> dict[str, list[GuildActiveTournamentConfig]]:
    grouped: dict[str, list[GuildActiveTournamentConfig]] = {}
    for tournament in tournaments:
        grouped.setdefault(tournament.config_hash, []).append(tournament)
    return grouped
