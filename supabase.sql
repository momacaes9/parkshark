-- ParkShark Duke - Supabase schema
-- Run in Supabase -> SQL Editor. Sensors / the Pi gateway write with the
-- service_role key (server-side only). The dashboard reads with the anon key.

-- 1) Occupancy observations (one row per sensor reading or per lot estimate)
create table if not exists public.occupancy (
  id          bigserial primary key,
  lot_id      text        not null,              -- OSM id from data/parking.geojson, e.g. 'w32336367'
  spot_id     text,                              -- e.g. 'w32336367-e12' (null = lot-level estimate)
  occupied    boolean,                           -- spot-level reading
  occupancy   real check (occupancy between 0 and 1),  -- lot-level fraction (AI estimator)
  confidence  real check (confidence between 0 and 1),
  source      text not null default 'sensor',    -- 'radar' | 'magnetometer' | 'estimator' | 'manual'
  device_id   text,
  observed_at timestamptz not null default now()
);
create index if not exists occupancy_spot_time on public.occupancy (lot_id, spot_id, observed_at desc);

-- 2) Latest state per spot (and per lot for lot-level rows)
create or replace view public.occupancy_latest
with (security_invoker = true) as
select distinct on (lot_id, coalesce(spot_id, ''))
       lot_id, spot_id, occupied, occupancy, confidence, source, observed_at
from public.occupancy
where observed_at > now() - interval '2 hours'
order by lot_id, coalesce(spot_id, ''), observed_at desc;

-- 3) Row Level Security: anyone may READ occupancy, nobody may write with the anon key
alter table public.occupancy enable row level security;
drop policy if exists "public read occupancy" on public.occupancy;
create policy "public read occupancy" on public.occupancy
  for select to anon, authenticated using (true);
grant select on public.occupancy to anon, authenticated;
grant select on public.occupancy_latest to anon, authenticated;
-- (no insert/update/delete policies -> only service_role can write)

-- 4) Privacy: plate sightings are stored ONLY as salted SHA-256 hashes, in a
--    separate table with RLS on and NO public policy (not readable by anon).
create table if not exists public.vehicle_events (
  id          bigserial primary key,
  lot_id      text not null,
  plate_hash  text not null,          -- sha256(per-session salt || plate); raw plates and images never leave the Pi
  event       text not null check (event in ('enter', 'exit')),
  observed_at timestamptz not null default now()
);
alter table public.vehicle_events enable row level security;
revoke all on public.vehicle_events from anon, authenticated;

-- Example test row (run once to see a lot turn "live" on the dashboard):
-- insert into public.occupancy (lot_id, occupancy, source) values ('w32336367', 0.95, 'manual');

-- Retention (see privacy.html): delete hashed events after 30 days, e.g. with pg_cron:
-- select cron.schedule('purge-vehicle-events', '0 4 * * *', $$delete from public.vehicle_events where observed_at < now() - interval '30 days'$$);
