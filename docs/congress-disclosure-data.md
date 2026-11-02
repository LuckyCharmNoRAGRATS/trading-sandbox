# Congressional Trading Disclosure Data Connector

> **E1.16 STATUS: NOT ACTIVE.** This page describes the original connector design. **Do not follow its earlier activation instructions.** The only authorized E1.16 workflow performs offline tests; no API fetch, database ingest, schedule, merge or trading has been approved. See [the E1.16 readiness and approval runbook](E1_16_CONGRESS_SYNC_READINESS.md).

## Status

- **Data ingestion only; paper and live brokerage orders are NOT submitted.**
- Default monitored person in the hourly workflow: **Nancy Pelosi**.
- Scheduled provider: Tracefour Nancy Pelosi member disclosures API (read-only, keyless, refreshed hourly). CongressInvests remains a selectable alternative. Neither is the unconnected pelosi.fun upstream API.
- Storage: the existing `trading-sandbox` Supabase project, in private `politician_disclosures` and `politician_sync_runs` tables.
- Primary source: https://tracefour.com/api-docs/congress-trading-api (keyless 60 requests/hour). Source compilation CC BY 4.0; retain attribution links. Alternative: https://congressinfor-production.up.railway.app/.
- Sync interval: hourly at minute 17 (GitHub Actions scheduled runs can be delayed).
- No stored records yet until a real ingest succeeds.

## Original activation proposal — blocked pending explicit approval

1. The SQL from `sql/congressional_disclosure_ingestion.sql` has already been applied to the connected Supabase project. Check table permissions and RLS before use elsewhere.
2. **Do not set or use production secrets for E1.16 offline tests.** A future specifically authorized one-time ingest would require server-only secrets:
   - `SUPABASE_URL`: the URL of the existing trading-sandbox Supabase project.
   - `SUPABASE_SERVICE_KEY`: a Supabase service-role/secret key. **Never expose in frontend, commit, logs, or chat.**
3. **Do not merge this branch.** This proposed activation step is not approved.
4. **Do not dispatch ingestion.** The E1.16 branch replaces the old ingest workflow with offline tests only.
5. **No hourly sync is enabled.** Scheduling would require separate approval after a controlled first ingest.

Optional local test:
```bash
python -m pip install requests==2.34.2 supabase==2.33.0 pytest==9.1.1
python -m pytest -q tests/test_congress_ingest.py
python congress_ingest.py --offline-fixture tests/fixtures/tracefour_pelosi_synthetic.json
```

Authenticated import is **disabled pending explicit approval**. The E1.16 runbook documents the future gated process; do not run it now.

## Point-in-time data access from Quant AI\n\nThe separate read-only module `congress_data_layer.py` exposes `filings_asof(db, asof=aware_datetime)` and `disclosure_candidates(db, asof=aware_datetime)`. Only filings first observed by our system before the supplied UTC timestamp are returned, so research does not gain access to subsequently ingested records. Candidate filtering accepts explicitly typed common stock/equity only, rejecting option records and unknown asset types. This is not a trading signal and no orders are generated.\n\n## Query stored data

```sql
select member_name, ticker, trade_type, transaction_date,
       filing_date, first_seen_at, amount_range, asset_type, owner_type, filing_url
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
- The Tracefour member endpoint returns at most 500 disclosed transactions. If the source grows beyond that cap, a full historical export or paging strategy is needed. The optional CongressInvests recent-trades endpoint can also truncate global results.
- Hourly polling is not a guarantee of hourly new official filings; upstream refresh lag and GitHub schedule delays can be significant.
- The database tables have RLS enabled and no anonymous/authenticated access. The backend service-role credential is intended for server use only.
- No ingestion of the requested Pelosi.fun RapidAPI source is attempted until its authenticated API endpoints, license, and schema are provided and validated. Tracefour's compilation requires source attribution (CC BY 4.0).\n- The `asset_type`, `owner_type`, and `upstream_ingested_at` provenance fields were added in a second applied Supabase migration; stock options are excluded from ordinary equity candidates.

## Next milestone

Verify a live successful API sync, validate coverage against official filings, add ingestion health alerts, benchmark trade signals with filing-date-safe backtests, and only then design a separate Alpaca **paper-only** execution integration.
