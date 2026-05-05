create table if not exists tournament_configs (
    id bigserial primary key,
    guild_id text not null,
    tournament_id text not null,
    tournament_name text not null,
    schema_version text not null,
    config_hash text not null,
    config jsonb not null,
    imported_by_user_id text,
    imported_at timestamptz not null default now(),
    unique (guild_id, tournament_id, config_hash)
);

create table if not exists guild_tournament_state (
    guild_id text primary key,
    active_tournament_config_id bigint not null references tournament_configs (id),
    updated_at timestamptz not null default now()
);

create index if not exists tournament_configs_guild_imported_at_idx
    on tournament_configs (guild_id, imported_at desc);
