create table if not exists provider_response_cache (
    id bigserial primary key,
    provider text not null,
    tournament_id text not null,
    config_hash text not null,
    fetched_match_count integer not null default 0,
    request_metadata jsonb not null default '{}'::jsonb,
    response_payload jsonb not null default '{}'::jsonb,
    fetched_at timestamptz not null default now()
);

create index if not exists provider_response_cache_provider_config_idx
    on provider_response_cache (provider, config_hash, fetched_at desc);

create table if not exists result_sync_warnings (
    id bigserial primary key,
    provider text not null,
    config_hash text not null,
    provider_match_id text not null,
    warning_type text not null,
    details jsonb not null default '{}'::jsonb,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    unique (provider, config_hash, provider_match_id, warning_type)
);

create index if not exists result_sync_warnings_provider_config_idx
    on result_sync_warnings (provider, config_hash, last_seen_at desc);
