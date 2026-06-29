create index if not exists prediction_entries_submitted_leaderboard_idx
    on prediction_entries (guild_id, tournament_config_id)
    where submitted_data is not null;

create index if not exists prediction_scores_guild_tournament_rank_idx
    on prediction_scores (
        guild_id,
        tournament_config_id,
        total_points desc,
        recalculated_at asc,
        lower(display_name) asc,
        user_id asc
    );

create index if not exists result_sync_runs_guild_tournament_started_at_idx
    on result_sync_runs (guild_id, tournament_config_id, started_at desc);
