-- Already applied to connected trading-sandbox Supabase.
-- Needed to keep reported stock options distinct from common-stock purchases.
alter table public.politician_disclosures
  add column if not exists asset_type text,
  add column if not exists owner_type text,
  add column if not exists upstream_ingested_at timestamptz;
