alter table guild_settings
    alter column live_results_provider set default 'fifa_public_calendar';

update guild_settings
set live_results_provider = 'fifa_public_calendar',
    updated_at = now()
where live_results_provider = 'football_data_org';
