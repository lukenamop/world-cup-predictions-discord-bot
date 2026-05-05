create table if not exists guild_settings (
    guild_id text primary key,
    announcement_channel_id text,
    leaderboard_channel_id text,
    timezone text not null default 'America/Indiana/Indianapolis',
    scoring_rules jsonb not null default '{}'::jsonb,
    privacy_defaults jsonb not null default '{"share_full_bracket": false}'::jsonb,
    live_results_provider text not null default 'fifa_public_calendar',
    lock_mode text not null default 'full_bracket_lock',
    lock_deadline_utc timestamptz,
    predictions_open boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists bot_health (
    id boolean primary key default true,
    bot_env text not null,
    last_started_at timestamptz,
    last_ready_at timestamptz,
    last_guild_count integer,
    last_command_sync_at timestamptz,
    check (id)
);

create table if not exists audit_log (
    id bigserial primary key,
    guild_id text,
    actor_user_id text,
    action text not null,
    details jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists audit_log_guild_created_at_idx
    on audit_log (guild_id, created_at desc);
