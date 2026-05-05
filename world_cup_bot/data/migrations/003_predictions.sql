create table if not exists prediction_entries (
    id bigserial primary key,
    guild_id text not null,
    tournament_config_id bigint not null references tournament_configs (id),
    user_id text not null,
    display_name text not null,
    draft_data jsonb not null default '{}'::jsonb,
    submitted_data jsonb,
    revision integer not null default 0,
    draft_updated_at timestamptz not null default now(),
    submitted_at timestamptz,
    submitted_updated_at timestamptz,
    created_at timestamptz not null default now(),
    unique (guild_id, tournament_config_id, user_id)
);

create table if not exists prediction_history (
    id bigserial primary key,
    prediction_entry_id bigint not null references prediction_entries (id),
    revision integer not null,
    event_type text not null,
    actor_user_id text not null,
    data jsonb not null,
    created_at timestamptz not null default now(),
    unique (prediction_entry_id, revision)
);

create index if not exists prediction_entries_guild_tournament_idx
    on prediction_entries (guild_id, tournament_config_id);

create index if not exists prediction_history_entry_created_at_idx
    on prediction_history (prediction_entry_id, created_at desc);
