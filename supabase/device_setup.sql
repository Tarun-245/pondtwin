-- Applied to the deployed project as provisioned_devices_and_atomic_pond_save.
-- The function was updated by partial_sensor_devices on 2026-10-06.
-- Run after setup.sql for a NEW project. Device keys are registered separately.
begin;

create table public.provisioned_devices (
    channel_id integer primary key check (channel_id > 0),
    owner_id uuid not null references auth.users(id) on delete cascade,
    label text not null default '',
    read_api_key text not null default '' check (read_api_key ~ '^[A-Za-z0-9]{0,128}$'),
    field_map jsonb not null check (jsonb_typeof(field_map) = 'object'),
    created_at timestamptz not null default now()
);
create index provisioned_devices_owner on public.provisioned_devices(owner_id);
alter table public.provisioned_devices enable row level security;
create policy devices_assigned_owner on public.provisioned_devices for select to authenticated
    using ((select auth.uid()) = owner_id);
revoke all on public.provisioned_devices from public, anon, authenticated;
grant select on public.provisioned_devices to authenticated;

alter table public.readings add column turbidity_voltage double precision;

-- A caller's JWT/RLS applies throughout this transaction. Never SECURITY DEFINER.
create or replace function public.save_pond(p_pond jsonb, p_connection jsonb default null, p_pond_id uuid default null)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    v_owner uuid := auth.uid();
    v_pond public.ponds;
    v_previous public.ponds;
    v_connection public.pond_connections;
    v_map jsonb;
begin
    if v_owner is null then
        raise insufficient_privilege using message = 'Sign in to save a pond.';
    end if;
    if p_pond_id is not null then
        select * into v_previous from public.ponds where id = p_pond_id and owner_id = v_owner for update;
        if not found then
            raise exception using errcode = 'PT404', message = 'Pond not found.';
        end if;
        v_pond := pg_catalog.jsonb_populate_record(v_previous, p_pond);
        v_pond.id := v_previous.id;
        v_pond.created_at := v_previous.created_at;
    else
        v_pond := pg_catalog.jsonb_populate_record(null::public.ponds, p_pond);
        v_pond.id := gen_random_uuid();
        v_pond.created_at := now();
    end if;
    v_pond.owner_id := v_owner;
    if p_connection is not null then
        v_map := p_connection->'field_map';
        if jsonb_typeof(v_map) is distinct from 'object' then
            raise check_violation using message = 'Invalid sensor mapping.';
        end if;
        if (select count(*) from jsonb_each_text(v_map)) not between 1 and 5
           or (select bool_and(key = any(array['water_temperature','ph','dissolved_oxygen','turbidity','turbidity_voltage'])) from jsonb_each_text(v_map)) is not true
           or (select count(distinct value::integer) from jsonb_each_text(v_map) where value::integer between 1 and 8) <> (select count(*) from jsonb_each_text(v_map)) then
            raise check_violation using message = 'Invalid sensor mapping.';
        end if;
        select * into v_connection from public.pond_connections where pond_id = v_pond.id and owner_id = v_owner;
        if found and (v_connection.channel_id <> (p_connection->>'channel_id')::integer or v_connection.field_map <> v_map)
           and exists(select 1 from public.readings where pond_id = v_pond.id and owner_id = v_owner) then
            raise exception using errcode = 'PT409', message = 'Sensor history cannot be mixed.';
        end if;
    end if;
    if p_pond_id is null then
        insert into public.ponds select (v_pond).*;
    else
        update public.ponds set
            name = v_pond.name, latitude = v_pond.latitude, longitude = v_pond.longitude,
            length_m = v_pond.length_m, width_m = v_pond.width_m, depth_m = v_pond.depth_m,
            species = v_pond.species, stock_count = v_pond.stock_count, avg_weight_g = v_pond.avg_weight_g,
            aerator_count = v_pond.aerator_count, aerator_kw = v_pond.aerator_kw, power_cost = v_pond.power_cost
        where id = v_pond.id and owner_id = v_owner;
    end if;
    if p_connection is not null then
        insert into public.pond_connections (pond_id, owner_id, channel_id, read_api_key, field_map)
        values (v_pond.id, v_owner, (p_connection->>'channel_id')::integer, coalesce(p_connection->>'read_api_key',''), v_map)
        on conflict (pond_id) do update set
            channel_id = excluded.channel_id, read_api_key = excluded.read_api_key,
            field_map = excluded.field_map, synced_at = null;
    end if;
    return to_jsonb(v_pond);
end;
$$;
revoke execute on function public.save_pond(jsonb,jsonb,uuid) from public, anon;
grant execute on function public.save_pond(jsonb,jsonb,uuid) to authenticated;
commit;
