-- Initial database setup for a NEW PondTwin project.
-- Run once only for a fresh database; the deployed project already has this schema.
begin;

create table public.ponds (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    name text not null check (length(btrim(name)) between 1 and 80),
    latitude double precision not null check (latitude between -90 and 90),
    longitude double precision not null check (longitude between -180 and 180),
    length_m double precision not null check (length_m > 1 and length_m <= 1000),
    width_m double precision not null check (width_m > 1 and width_m <= 1000),
    depth_m double precision not null check (depth_m > 0.3 and depth_m <= 8),
    species text not null check (species in ('tilapia','vannamei','carp','pangasius','seabass')),
    stock_count integer not null check (stock_count between 0 and 5000000),
    avg_weight_g double precision not null check (avg_weight_g > 0 and avg_weight_g <= 20000),
    aerator_count integer not null check (aerator_count between 0 and 20),
    aerator_kw double precision not null check (aerator_kw between 0 and 50),
    power_cost double precision not null check (power_cost between 0 and 100),
    created_at timestamptz not null default now(),
    unique (id, owner_id)
);
create index ponds_owner_created on public.ponds(owner_id, created_at);

create table public.pond_connections (
    pond_id uuid primary key,
    owner_id uuid not null,
    channel_id integer not null check (channel_id > 0),
    read_api_key text not null default '',
    field_map jsonb not null,
    synced_at timestamptz,
    foreign key (pond_id, owner_id) references public.ponds(id, owner_id) on delete cascade
);
create index pond_connections_owner on public.pond_connections(owner_id);
create index pond_connections_pond_owner on public.pond_connections(pond_id, owner_id);

create table public.readings (
    id bigint generated always as identity primary key,
    pond_id uuid not null,
    owner_id uuid not null,
    entry_id bigint not null check (entry_id > 0),
    ts timestamptz not null,
    water_temperature double precision,
    ph double precision,
    dissolved_oxygen double precision,
    turbidity double precision,
    quality text not null check (quality in ('ok','missing','spike','out_of_range')),
    source text not null default 'thingspeak' check (source = 'thingspeak'),
    foreign key (pond_id, owner_id) references public.ponds(id, owner_id) on delete cascade,
    unique (pond_id, entry_id)
);
create index readings_owner_pond_time on public.readings(owner_id, pond_id, ts desc, entry_id desc);
create index readings_pond_owner on public.readings(pond_id, owner_id);

create table public.forecasts (
    id uuid primary key default gen_random_uuid(),
    pond_id uuid not null,
    owner_id uuid not null,
    issued_at timestamptz not null default now(),
    horizon_hours integer not null check (horizon_hours between 1 and 48),
    engine text not null,
    payload jsonb not null,
    foreign key (pond_id, owner_id) references public.ponds(id, owner_id) on delete cascade
);
create index forecasts_owner_pond_time on public.forecasts(owner_id, pond_id, issued_at desc);
create index forecasts_pond_owner on public.forecasts(pond_id, owner_id);

alter table public.ponds enable row level security;
alter table public.pond_connections enable row level security;
alter table public.readings enable row level security;
alter table public.forecasts enable row level security;

-- Every table has its own owner check. Composite foreign keys prevent a farmer
-- from attaching records/credentials to somebody else's pond.
create policy ponds_owner on public.ponds to authenticated
    using ((select auth.uid()) = owner_id)
    with check ((select auth.uid()) = owner_id);
create policy connections_owner on public.pond_connections to authenticated
    using ((select auth.uid()) = owner_id)
    with check ((select auth.uid()) = owner_id);
create policy readings_owner on public.readings to authenticated
    using ((select auth.uid()) = owner_id)
    with check ((select auth.uid()) = owner_id);
create policy forecasts_owner on public.forecasts to authenticated
    using ((select auth.uid()) = owner_id)
    with check ((select auth.uid()) = owner_id);

revoke all on public.ponds, public.pond_connections, public.readings, public.forecasts from anon, public;
grant usage on schema public to authenticated;
grant select, insert, update, delete on public.ponds, public.pond_connections to authenticated;
grant select, insert on public.readings, public.forecasts to authenticated;
grant usage, select on sequence public.readings_id_seq to authenticated;
commit;
