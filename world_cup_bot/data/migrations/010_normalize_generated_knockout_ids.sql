create function _migrate_legacy_knockout_round_ids(entries jsonb, prefix text)
returns jsonb
language sql
immutable
as $$
    select case
        when jsonb_typeof(entries) <> 'array' then entries
        when exists (
            select 1
            from jsonb_array_elements(entries) as entry(value)
            where entry.value->>'match_id' in (
                prefix || '-2',
                prefix || '-4',
                prefix || '-6',
                prefix || '-8'
            )
        ) then entries
        else (
            select coalesce(
                jsonb_agg(
                    case
                        when jsonb_typeof(entry.value) = 'object'
                            and replacement.new_id is not null
                        then jsonb_set(
                            entry.value,
                            '{match_id}',
                            to_jsonb(replacement.new_id),
                            false
                        )
                        else entry.value
                    end
                    order by entry.ordinality
                ),
                '[]'::jsonb
            )
            from jsonb_array_elements(entries) with ordinality as entry(value, ordinality)
            left join (
                values
                    (prefix || '-1', prefix || '-1'),
                    (prefix || '-3', prefix || '-2'),
                    (prefix || '-5', prefix || '-3'),
                    (prefix || '-7', prefix || '-4'),
                    (prefix || '-9', prefix || '-5'),
                    (prefix || '-11', prefix || '-6'),
                    (prefix || '-13', prefix || '-7'),
                    (prefix || '-15', prefix || '-8')
            ) as replacement(old_id, new_id)
                on entry.value->>'match_id' = replacement.old_id
        )
    end
$$;

create function _migrate_legacy_knockout_ids(data jsonb)
returns jsonb
language sql
immutable
as $$
    select case
        when jsonb_typeof(data) <> 'object'
            or jsonb_typeof(data->'knockout') <> 'object'
        then data
        else jsonb_set(
            data,
            '{knockout}',
            (data->'knockout')
                || case
                    when (data->'knockout') ? 'round_of_16'
                    then jsonb_build_object(
                        'round_of_16',
                        _migrate_legacy_knockout_round_ids(
                            data #> '{knockout,round_of_16}',
                            'R16'
                        )
                    )
                    else '{}'::jsonb
                end
                || case
                    when (data->'knockout') ? 'quarter_finals'
                    then jsonb_build_object(
                        'quarter_finals',
                        _migrate_legacy_knockout_round_ids(
                            data #> '{knockout,quarter_finals}',
                            'QF'
                        )
                    )
                    else '{}'::jsonb
                end
                || case
                    when (data->'knockout') ? 'semi_finals'
                    then jsonb_build_object(
                        'semi_finals',
                        _migrate_legacy_knockout_round_ids(
                            data #> '{knockout,semi_finals}',
                            'SF'
                        )
                    )
                    else '{}'::jsonb
                end,
            false
        )
    end
$$;

update prediction_entries
set
    draft_data = _migrate_legacy_knockout_ids(draft_data),
    submitted_data = case
        when submitted_data is null then null
        else _migrate_legacy_knockout_ids(submitted_data)
    end
where jsonb_typeof(draft_data->'knockout') = 'object'
    or jsonb_typeof(submitted_data->'knockout') = 'object';

update prediction_history
set data = _migrate_legacy_knockout_ids(data)
where jsonb_typeof(data->'knockout') = 'object';

drop function _migrate_legacy_knockout_ids(jsonb);
drop function _migrate_legacy_knockout_round_ids(jsonb, text);
