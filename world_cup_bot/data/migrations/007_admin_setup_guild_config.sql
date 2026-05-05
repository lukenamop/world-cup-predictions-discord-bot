do $$
begin
    if exists (
        select 1
        from information_schema.columns
        where table_schema = current_schema()
            and table_name = 'guild_settings'
            and column_name = 'prediction_channel_id'
    ) and not exists (
        select 1
        from information_schema.columns
        where table_schema = current_schema()
            and table_name = 'guild_settings'
            and column_name = 'announcement_channel_id'
    ) then
        alter table guild_settings
            rename column prediction_channel_id to announcement_channel_id;
    end if;
end $$;
