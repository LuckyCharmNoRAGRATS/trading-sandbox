# Congressional Trading Disclosure Data Connector

## Status

- **Data ingestion only; paper and live brokerage orders are NOT submitted.**
- Default monitored person in the hourly workflow: **Nancy Pelosi**.
- Live provider: CongressInvests API. This is a documented alternative to pelosi.fun, whose authenticated upstream endpoint is not connected here.
- Storage: the existing `trading-sandbox` Supabase project, in private `politician_disclosures` and `politician_sync_runs` tables.
- Source: https://congressinfor-production.up.railway.app/ (100 requests/day free tier as documented on Nov 2, 2026).
- Sync interval: hourly at minute 17 (GitHub Actions scheduled runs can be delayed).
- No stored records yet until a real ingest succeeds.

## Installation and activation

1. The SQL from `sql/congressional_disclosure_ingestion.sql` has already been applied to the connected Supabase project. Check table permissions and RLS before use elsewhere.
2. Set GitHub **Actions repository secrets** (Settings → Secrets and variables → Actions):
   - `SUPABASE_URL`: the URL of the existing trading-sandbox Supabase project.
   - `SUPABASE_SERVICE_KEY`: a Supabase service-role/secret key. **Never expose in frontend, commit, logs, or chat.**
3. Merge this branch after reviewing it. GitHub cron workflows run from the default branch only.
4. Use Actions → *Congressional disclosures sync (data only)* → Run workflow. Check output, then query `politician_sync_runs`.
5. Normal hourly sync will run after deployment. It does not execute trades.

Optional local test:
```bash
python -m pip install requests==2.34.2 supabase==2.33.0 pytest==9.1.1
python -m pytest -q tests/test_congress_ingest.py
python congress_ingest.py --days 30 --limit 500 --dry-run
```

Authenticated import (run securely with environment credentials):
```bash
python congress_ingest.py --days 90 --limit 500 --member 'Nancy Pelosi'
```

## Query stored data

```sql
select member_name, ticker, trade_type, transaction_date,
       filing_date, first_seen_at, amount_range, filing_url
from public.politician_disclosures
where member_name ilike '%Pelosi%'
order by filing_date desc, first_seen_at desc
limit 100;
```

Health checks:
```sql
select * from public.politician_sync_runs order by started_at desc limit 10;
```

## Important data caveats

- A politician does not publish trades in real time. They generally disclose after the transaction date, sometimes weeks later. `first_seen_at` is the first time **our system** observed the row, not proof of its earliest public availability.
- Source reports may include trades by spouses or dependents, options, and monetary ranges instead of exact numbers of shares. No exact order replication is possible from bracketed disclosures.
- For backtests, **never assume the trade was knowable at `transaction_date`**. In the absence of exact publication timestamps, use a conservatively delayed first executable price.
- Rows lacking valid required dates are rejected. No silent fallback to transaction date as filing date.
- `event_id` is a SHA-256 of provider identifier or disclosed fields. If the provider omits stable IDs, perfectly identical same-day records may collapse. This limitation should be addressed via source filing IDs before making performance claims.
- Feeds can be truncated by their maximum result limit and provider history window. For complete long-history backfills, add validated paging or source-specific historical exports.
- Hourly polling is not a guarantee of hourly new official filings; upstream refresh lag and GitHub schedule delays can be significant.
- The database tables have RLS enabled and no anonymous/authenticated access. The backend service-role credential is intended for server use only.
- No ingestion of the requested Pelosi.fun RapidAPI source is attempted until its authenticated API endpoints, license, and schema are provided and validated. Source attribution must be retained.

## Next milestone

Verify a live successful API sync, validate coverage against official filings, add ingestion health alerts, benchmark trade signals with filing-date-safe backtests, and only then design a separate Alpaca **paper-only** execution integration.
