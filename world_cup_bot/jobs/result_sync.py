from __future__ import annotations

import logging
from typing import Any

from world_cup_bot.data.repositories import TournamentConfigRepository
from world_cup_bot.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardServiceError,
)
from world_cup_bot.services.result_sync_service import (
    ResultSyncService,
    ResultSyncServiceError,
)


LOGGER = logging.getLogger(__name__)


async def sync_all_active_guilds(bot: Any) -> None:
    guild_ids = await TournamentConfigRepository(
        bot.database.pool
    ).list_active_guild_ids()
    for guild_id in guild_ids:
        try:
            summary = await ResultSyncService(
                bot.database.pool,
                provider_name=bot.settings.live_results_provider,
                api_key=bot.settings.live_results_api_key,
            ).sync_guild(guild_id=guild_id)
            recalculation = await LeaderboardService(bot.database.pool).recalculate(
                guild_id=guild_id
            )
        except (ResultSyncServiceError, LeaderboardServiceError):
            LOGGER.exception("Result sync job failed for guild_id=%s", guild_id)
            continue

        LOGGER.info(
            "Result sync job complete; guild_id=%s fetched=%s applied=%s scored=%s scoring_version=%s",
            guild_id,
            summary.fetched_match_count,
            summary.applied_match_count,
            recalculation.scored_prediction_count,
            recalculation.scoring_version,
        )
