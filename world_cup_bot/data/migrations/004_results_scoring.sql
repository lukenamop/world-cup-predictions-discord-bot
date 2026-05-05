create table if not exists match_results (
    id bigserial primary key,
    guild_id text not null,
    tournament_config_id bigint not null references tournament_configs (id),
    match_id text not null,
    provider text not null,
    provider_match_id text,
    stage text not null,
    round_name text,
    group_id text,
    home_team_id text not null,
    away_team_id text not null,
    home_score integer,
    away_score integer,
    status text not null,
    winner_team_id text,
    played_at timestamptz,
    provider_payload jsonb not null default '{}'::jsonb,
    synced_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, tournament_config_id, match_id)
);

create index if not exists match_results_guild_tournament_idx
    on match_results (guild_id, tournament_config_id);

create index if not exists match_results_provider_match_idx
    on match_results (provider, provider_match_id);

create table if not exists result_sync_runs (
    id bigserial primary key,
    guild_id text not null,
    tournament_config_id bigint not null references tournament_configs (id),
    provider text not null,
    status text not null,
    fetched_match_count integer not null default 0,
    applied_match_count integer not null default 0,
    warning_count integer not null default 0,
    details jsonb not null default '{}'::jsonb,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists result_sync_runs_guild_started_at_idx
    on result_sync_runs (guild_id, started_at desc);

create table if not exists prediction_scores (
    prediction_entry_id bigint primary key references prediction_entries (id),
    guild_id text not null,
    tournament_config_id bigint not null references tournament_configs (id),
    user_id text not null,
    display_name text not null,
    total_points integer not null,
    group_points integer not null,
    knockout_points integer not null,
    breakdown jsonb not null,
    scoring_version text not null,
    recalculated_at timestamptz not null default now()
);

create index if not exists prediction_scores_guild_tournament_points_idx
    on prediction_scores (guild_id, tournament_config_id, total_points desc, recalculated_at desc);
