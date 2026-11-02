-- Mirror of the deployed 2026-11-02 schema change in Supabase trading-sandbox.
-- Raw public disclosures only. No orders, broker keys or live trading side effects.
create table if not exists public.politician_disclosures (
  event_id text primary key check (event_id ~ '^[0-9a-f]{64}$'),
  provider text not null,
  member_name text not null,
  chamber text,
  ticker text,
  trade_type text not null,
  asset_description text,
  amount_range text,
  transaction_date date not null,
  filing_date date not null,
  filing_url text,
  raw_record jsonb not null,
  first_seen_at timestamptz not null default now(),
  constraint politician_disclosures_trade_type_check
    check (trade_type in ('buy', 'sell', 'exchange', 'other'))
);
create index if not exists politician_disclosures_filing_idx
  on public.politician_disclosures (filing_date desc, first_seen_at desc);
create index if not exists politician_disclosures_member_idx
  on public.politician_disclosures (member_name, ticker, filing_date desc);
alter table public.politician_disclosures enable row level security;
revoke all on public.politician_disclosures from anon, authenticated;
grant select, insert, update, delete on public.politician_disclosures to service_role;

create table if not exists public.politician_sync_runs (
  run_id bigint generated always as identity primary key,
  provider text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  fetched integer not null default 0,
  accepted integer not null default 0,
  status text not null check (status in ('running', 'success', 'failed')),
  error_message text
);
create index if not exists politician_sync_runs_started_idx
  on public.politician_sync_runs (started_at desc);
alter table public.politician_sync_runs enable row level security;
revoke all on public.politician_sync_runs from anon, authenticated;
grant select, insert, update on public.politician_sync_runs to service_role;
grant usage, select on sequence public.politician_sync_runs_run_id_seq to service_role;
