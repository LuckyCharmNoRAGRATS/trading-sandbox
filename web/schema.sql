-- Run once in Supabase → SQL Editor. Replace YOU@example.com (twice) with the email you log in with.

create table kv       (key text primary key, data jsonb not null default '{}', updated_at timestamptz default now());
create table signals  (ticker text primary key, strategy text, status text, enabled boolean default true,
                       metrics jsonb default '{}', updated_at timestamptz default now());
create table results  (id bigserial primary key, kind text, name text, metrics jsonb, created_at timestamptz default now());
create table logs     (id bigserial primary key, ts timestamptz default now(), level text, source text, msg text);
create table commands (id bigserial primary key, cmd text not null, args jsonb default '{}', source text default 'web',
                       status text default 'pending', result text, created_at timestamptz default now());

-- Only your login can read anything. The VM uses the service key, which bypasses these rules.
do $$ declare t text; begin
  foreach t in array array['kv','signals','results','logs','commands'] loop
    execute format('alter table %I enable row level security', t);
    execute format($p$create policy me_read on %I for select to authenticated
                      using (auth.jwt()->>'email' = 'YOU@example.com')$p$, t);
  end loop;
end $$;

-- Drop the old command policy and replace it with the expanded Alpaca + Live Edit command list
drop policy if exists me_cmd on commands;

create policy me_cmd on commands for insert to authenticated
  with check (auth.jwt()->>'email' = 'YOU@example.com'
    and cmd in (
      'ai_veto','cancelall','resume','toggle','run_backtest',
      'run_grid_search','refresh_stats','discord_test',
      'submit_order','close_position','cancel_order','update_params','sync_alpaca'
    ));

-- The website can only queue these commands, nothing else.
create policy me_cmd on commands for insert to authenticated
  with check (auth.jwt()->>'email' = 'YOU@example.com'
    and cmd in ('ai_veto','cancelall','resume','toggle','run_backtest','run_grid_search','refresh_stats','discord_test'));

-- Push changes to the browser in real time.
alter publication supabase_realtime add table kv, signals, results, logs, commands;
