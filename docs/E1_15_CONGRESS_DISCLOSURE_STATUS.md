# E1.15 — Pelosi / Congressional Disclosure Integration Status

**Date:** 2026-11-02. **Research status:** IMPLEMENTATION PRESENT, INGESTION NOT OBSERVED. **Execution status:** NO-GO, disabled.

## Inspected branches (read-only)

- `feature/congress-disclosure-data`: isolated feature with `congress_ingest.py`, `congress_data_layer.py`, tests, a planned hourly GitHub Actions ingest workflow and SQL schema files. It is 16 commits ahead of `main` as observed; the feature has **not** been merged into the E1 safety branch.
- `fix/e1-safe-execution`: the E1 safety work, still isolated. E1.15 adds a **new offline-only** `congress_research_guard.py` and synthetic tests, without copying the ingest scheduler, enabling requests, submitting orders or merging the branches.
- The primary feature source is **Tracefour** `GET /v1/congress/nancy-pelosi`. The `GET /v1/trackers/pelosi` endpoint is a reconstructed portfolio view, **not** the same payload. `CongressInvests` is a selectable alternate provider. The project's feature documentation explicitly says Pelosi.fun RapidAPI is *not connected*.

## Read-only Supabase verification (2026-11-02)

Project: `trading-sandbox`; no mutation performed.

| Check | Read-only result |
| --- | --- |
| `public.politician_disclosures` | Exists; 0 rows |
| `public.politician_sync_runs` | Exists; 0 rows |
| Both tables have row-level security | Yes |
| `anon` / `authenticated` SELECT on both tables | No |
| `service_role` SELECT on both tables | Yes |
| Observed successful API ingestion | **None** |

The feature branch's scheduled GitHub workflow runs only after a default-branch activation; its presence on a feature branch alone does not mean data collection started. Do not infer that GitHub secrets are configured or that API syncs passed. No actual disclosure time series or trading performance metrics are available from the project database.

## Source caveats

- Tracefour documents its Congress endpoint as **at most 500 transactions**. A full 500-record response is ambiguous without an independent full export or verified pagination. Its `/data/congress/nancy-pelosi.json` static export is documented as a full-member alternative; the export's live shape and completeness still require validation before relying on it.
- Public STOCK Act disclosures arrive after transaction dates (generally within 30–45 days); time-of-trade visibility must never be assumed.
- `transaction_date` and `filing_date` are different. `first_seen_at` records when *our system* first observed a row. `upstream_ingested_at` is **not** a substitute for our first-seen timestamp in an honest backtest.
- Transactions can be by spouses or dependents and may involve options or amount ranges. A disclosed stock option is not an ordinary equity trade; do not attribute every trade to Nancy Pelosi personally.
- Tracefour's compilation is CC BY 4.0 and requires attribution and underlying filing links.

Sources:
- https://tracefour.com/api-docs/congress-trading-api
- https://ethics.house.gov/financial-disclosure/

## E1.15 additions on execution-safety branch

`congress_research_guard.py` is pure/offline research code. It accepts an injected read-only DB handle, enforces an as-of timestamp with timezone, validates the first-seen cutoff, requires source filing provenance, excludes options and unknown asset types, and rejects ambiguous 500-row source or result boundaries. It never contacts Tracefour, Supabase, or Alpaca and never emits broker orders.

`tests/test_e115_congress_research_guard.py` supplies synthetic option/stock, family ownership, duplicate ID, wrong-person, future-leakage, provenance and truncation scenarios. Included in E1 isolated pytest CI; **a registered test is not proof of a passing CI result**.

This helper **is not yet wired into** `feature/congress-disclosure-data`. Any future integration requires review and approval; the existing feature code may not enforce every guard above.

## Executed local regression evidence

- Offline run on 2026-11-02: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_e115_congress_research_guard.py` — **29 passed in 0.08s** in a temporary test directory.
- Verified exact Git blob SHA of locally tested `congress_research_guard.py`: `620e2c22af00c07c3758ad4988521d29b8063f83`.
- Verified exact Git blob SHA of locally tested `tests/test_e115_congress_research_guard.py`: `775dd9b654e1746648a6322dd405f34829a63dec`.
- This is local evidence for the new isolated research guard **only**. The feature branch's existing ingestion tests, complete E1 execution tests, GitHub Actions, scheduled sync and database ingestion remain **unverified**.

## Remaining work / release gates

1. Verify the exact feature branch's offline tests and schema against actual provider documentation, with no live ingestion until approved.
2. Obtain actual successful GitHub Actions pytest and disposable PostgreSQL evidence for the safety branch; only Vercel statuses have been independently accessible so far.
3. Verify source attribution, completeness of 500-record member snapshots, date semantics and duplicate identity handling.
4. Review secrets and the existing schema for least privilege; do not expose `SUPABASE_SERVICE_KEY` in clients or UI.
5. After **explicit authorization**, consider turning on a data-only scheduler and verifying one controlled ingest run. Do not activate automated trading.
6. Only after genuine point-in-time histories exist, study disclosure-lag-adjusted, transaction-cost-aware backtests and out-of-sample validity. Do not make claims of alpha or replicable politician returns before that.

**Decision:** NO-GO for automated orders and for merging/deploying new workflows. Preserve existing `E1_EXECUTION_ENABLED=false`, no Supabase schema or data changes, no Alpaca API calls, no trading execution, no branch merge or intentional deployment.
