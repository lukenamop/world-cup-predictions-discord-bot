alter table guild_settings
    drop column if exists privacy_defaults;

drop table if exists user_preferences;
