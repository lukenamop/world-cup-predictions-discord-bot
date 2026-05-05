create table if not exists tie_breaker_adjudications (
    id bigserial primary key,
    tournament_id text not null,
    config_hash text not null,
    scope text not null check (scope in ('group', 'best_third')),
    scope_key text not null,
    team_set_key text not null,
    team_ids jsonb not null,
    ordered_team_ids jsonb not null,
    criterion text not null default 'operator_adjudication',
    reason text not null,
    actor_user_id text not null,
    created_at timestamptz not null default now(),
    unique (tournament_id, config_hash, scope, scope_key, team_set_key)
);

create index if not exists tie_breaker_adjudications_config_idx
    on tie_breaker_adjudications (tournament_id, config_hash, created_at desc);
