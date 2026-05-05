create table if not exists user_preferences (
    guild_id text not null,
    user_id text not null,
    share_full_bracket boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id)
);

create index if not exists user_preferences_guild_idx
    on user_preferences (guild_id);
