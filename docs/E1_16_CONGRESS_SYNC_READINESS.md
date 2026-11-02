# E1.16 — Pelosi Congressional Disclosure Ingestion Readiness

**Decision: PREPARED FOR REVIEW, NOT ACTIVATED.** No real provider data was fetched, no Supabase rows were inserted/updated, no scheduled sync was started, and no trading was enabled.

Date: 2026-11-02. Repository: `LuckyCharmNoRAGRATS/trading-sandbox`.

## Branch and integration boundary

- Base feature: `feature/congress-disclosure-data` (commit `562d95b761f9d551c88895f9ea79c7484a0a231f`).
- This milestone: **`feature/e116-congress-readiness`**, an isolated child of that feature. No merge into `main`, `feature/congress-disclosure-data` or `fix/e1-safe-execution` was performed.
- E1.15 research guard (`congress_research_guard.py`) and its regression tests were copied from `fix/e1-safe-execution` and integrated into `congress_data_layer.py` on the E1.16 branch. A disclosure remains **research data, never a trade instruction**.
- The E1.14 execution-safety branch and draft database fencing work remain unchanged and NO-GO.

## Evidence: read-only live database status

Connected `trading-sandbox` Supabase project, read-only verification on 2026-11-02:

| Measure | Result |
| --- | --- |
| `public.politician_disclosures` rows | **0** |
| `public.politician_sync_runs` rows | **0** |
| Latest successful sync timestamp | **NULL** |
| Schema present | Yes |
| Source of actual transactions | None ingested |

Previous project verification found RLS enabled and ordinary anonymous/authenticated `SELECT` unavailable. These are database access checks, **not** permission to ingest or trade. No schema or row writes were performed.

## Confirmed source/API contract

The feature uses Tracefour `GET /v1/congress/nancy-pelosi`, not the reconstructed `GET /v1/trackers/pelosi` portfolio view and not an authenticated Pelosi.fun API. Public reference: https://tracefour.com/api-docs/congress-trading-api . The provider documents a `data.member` object, `data.trades[]`, and `meta.fetchedAt`. **`meta.count` counts the member record, not the number of trades.** The member endpoint is capped at 500 records and has no validated continuation cursor; exactly 500 is quarantined instead of falsely declared complete. A future historical full-member export could be assessed separately: https://tracefour.com/api-docs/ .

The provider compilation is CC BY 4.0 and needs attribution (link to Tracefour) plus an original filing link. Filing date, transaction date, system `first_seen_at`, and provider `ingestedAt` are different concepts. Congressional trades can be disclosed weeks after the transaction. Spouse option disclosures **must not** be treated as ordinary Nancy Pelosi equity purchases or copied at transaction-date prices.

## Changes implemented, without activation

1. **Source envelope and freshness check:** `congress_quality.py` validates member and trade identity, original HTTPS filing links, a nonempty response, `meta.fetchedAt` no older than 6 hours, and no provider timestamp more than 5 minutes in the future. A fresh cache is **not** proof that a fresh congressional filing exists.
2. **Completeness check:** 500 returned Tracefour rows are rejected; so are empty feeds. The alternative CongressInvests endpoint requires a source timestamp and refuses an empty/full requested page.
3. **Ingestion fail-closed:** `congress_ingest.py` validates the complete candidate batch before any upsert; missing dates, conflicting provider event identities, unmatched member filters and oversized (>100 unique records) batches reject the write. At most one SQL upsert statement is used; sync-run bookkeeping is not transactional with that upsert and remains an operational limitation.
4. **CLI approval gate:** normal execution now refuses to construct the network session unless `--allow-network-fetch` was explicitly supplied. Writes additionally require `--approve-database-write` **and** an explicit `CONGRESS_SYNC_APPROVED=data-only` environment flag. **Neither was set or used.** These technical flags do not replace human authorization.
5. **Offline synthetic fixture mode:** `python congress_ingest.py --offline-fixture tests/fixtures/tracefour_pelosi_synthetic.json` reads a local synthetic JSON fixture only; it prints `live_data_verified=false`, `database_writes=false`. No real API connection occurs.
6. **Research access:** `congress_data_layer.py` delegates to E1.15 bounded as-of read and conservative equity-only candidate projections; an unhandled database page boundary throws rather than silently truncating a backtest.
7. **GitHub workflow:** `.github/workflows/congress-disclosures.yml` on the E1.16 branch is **offline validation only** on that branch, with **no schedule, dispatch ingestion, API invocation, Supabase service credentials, or broker commands**. Its artifact is `congress-e116-offline-junit`.

## Executed offline test evidence

Local isolated checkout assembled from **the exact GitHub source contents**; Git blob SHA checks performed for every changed source/test file:

```bash
PYTHONPATH=tests:. python -m pytest -p e116_network_guard -q \
  -p no:cacheprovider \
  tests/test_congress_ingest.py \
  tests/test_congress_data_layer.py \
  tests/test_tracefour_ingest.py \
  tests/test_e115_congress_research_guard.py \
  tests/test_e116_quality.py
```

**Result: 84 passed in 0.10s**. Local runtime: Python with pytest 9.0.2 and requests 2.32.5. GitHub Actions runner is configured for Python 3.12, pytest 9.1.1 and requests 2.34.2. **CI outcome has not been independently retrieved and MUST NOT be presented as green without a run ID, job conclusion and JUnit artifact.** No internet, official filing verification, or real Supabase writes were part of these tests.

## Controlled first-sync plan — requires NEW explicit approval

1. **Approval A — one read-only external source preview:** authorize a one-time Tracefour HTTP GET to the member endpoint. Check HTTP, timestamps, attribution, member ID, original filing links, duplicates, option ownership and <500 record coverage. Run an explicitly approved `--allow-network-fetch --dry-run` preview with zero database writes. Compare selected records with the original House PDF filings. Stop on any failure.
2. **Review findings:** verify there are 1–100 distinct eligible records (otherwise a separately reviewed transactional bulk path is required); inspect event-id stability and first-seen semantics; confirm the source terms and rate limits.
3. **Approval B — one data-only database ingest:** only after specific user authorization, use scoped server-side secrets and the one-time CLI write gate. Read back `politician_sync_runs` plus disclosure row counts, original links, and `first_seen_at`. Verify no automatic orders, duplicate rows or invalid dates. Stop immediately on unexpected results.
4. **Approval C — recurring schedule:** a **separate later decision** after a healthy first sync and alerting. Do not install or activate cron just because a one-time ingest succeeded. GitHub cron workflows run from the default branch; no merge is authorized today.

**Open blockers:** no observed real data, no verified live provider freshness, no independent CI job evidence, no end-to-end Supabase write/rollback test, no cross-source official-filing reconciliation, 500-record history cap, and no approved recurring service. **No trading or alpha claims.**

## Approval A recovery — offline saved-response validation

The initially approved read-only Tracefour HTTP GET could not complete from the assistant environment because of DNS resolution. **No actual JSON response was acquired, and no second HTTP GET was made.** E1.16 now includes `congress_preview.py`, which validates an already-downloaded source file using the **actual current UTC time**, rather than the historical fixture's cached timestamp. It reports source-cache age, unique source filing IDs, dates, reporting lags, asset types and ownership with no IO beyond reading the local file.

A new standalone offline test suite, `tests/test_e116_offline_preview.py`, adds 13 tests for stale/future caches, bad member IDs, malformed JSON, filing-ID collisions, invalid dates, option classification, and the 500-row boundary. **97 local offline tests passed in 0.15 seconds** with the network guard in place. The CI workflow was updated to register the sixth suite; GitHub Actions results have not yet been retrieved independently.

**Next user step:** save the API response locally with a one-time read-only `curl.exe` command and upload the JSON. Full instructions: [E1.16 local response validation](E1_16_LOCAL_SOURCE_PREVIEW.md). If the endpoint cannot be reached from the user's computer, provide the failure output or request permission to evaluate an alternative source; do not fabricate live data.

**Approval B/C remain blocked:** no Supabase writes, no recurring schedule, no trading and no merge or intentional deploy.
