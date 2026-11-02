# Quant Observability v1 — Master Architecture handoff

Status: **isolated, synthetic-only**. Branch: `feature/quant-observability`. No broker, database, webhook, dashboard, runtime or strategy modification is included.

## Source inspection (2026-11-02)

Inspected `README.md`, `quant_engine.py`, `live_paper_trader.py`, `dashboard_bridge.py`, `discord_notifier.py`, `main_discord.py`, `cogs/ai_agents.py`, `cogs/modelling.py`, `data_fetcher.py`, `stats_analyzer.py`, `risk_manager.py`, `Backtest.py`, `strategies/mean_reversion.py`, `web/schema.sql`, `vercel.json`, `package.json`, `.gitignore`, `index.html` (relevant dashboard and Supabase paths), `logs/trading_bot.log` (aggregate classifications only), and the current `tests/*` Python tests. No `.env` contents or live credentials were read. No production Supabase or Vercel environment was queried.

**Findings:** `dashboard_bridge.py` writes `kv.value`, `logs.message/timestamp`, reads `commands.command/payload` and updates `commands.message`, while `web/schema.sql` defines `kv.data`, `logs.msg/ts`, `commands.cmd/args/result`. `web/schema.sql` declares the `me_cmd` insert policy twice; this is not safe to run as-is. Existing `index.html` contains SIMULATED positions/account/returns and order-control commands. `ExecutionOMS` emits an `ORDER EXECUTED` alert after `submit_order`, before confirmed fill. `live_paper_trader.py` uses the manual `risk_engine` identifier before initialization. `discord_notifier.py` forwards raw error strings. Logs are plain text, not independently reconciled fill records. `.env` is already tracked in the public Git tree despite `.gitignore` rules; content intentionally not accessed. Treat all credential values as potentially exposed and arrange secure rotation / Git-history remediation separately; this PR must not alter shared files or secrets.

## Ownership and API boundaries

- **OMS / Risk** own order authorization, idempotent submission, broker state retrieval, execution gating, and decisions. `quant_observability` imports none of them and exposes no submit/cancel/sync methods.
- **Broker adapter (future separate PR, architecture sign-off required):** reads snapshots using least-privilege API scope and transforms them into `quant_observability.models` instances. No broker adapter is implemented here.
- **Event producers (future separate PR):** after OMS emits order lifecycle events, deliver immutable `OrderEvent` and `FillEvent` with trustworthy IDs and UTC timestamps. A submitted order is **not** a filled order.
- **Observability** is pure computation: `reconcile(...)`, `report(...)`, `freshness(...)`, `audit_metadata(...)`; no network, database, secrets, command queue, or filesystem imports/operations.
- **Dashboard writer/reader (future approval):** transport versioned, redacted aggregates from a separately reviewed read-only endpoint. Do not use existing `commands` table or mutate existing `kv`/`logs` schemas. RLS/tenant segregation and schema migration must be approved in a separate PR. No changes to Vercel or Supabase in this PR.
- **Discord notifications (future approval):** only publish severity, fixed code, event hash, and status; never exception strings, URLs, credentials, order commands, or raw market/broker payloads.

## Contract v1

All input dataclasses are frozen and have `Meta(event_id, observed_at, source, schema_version=1)` with safe identifiers and timezone-aware **UTC** timestamps. Currency is mandatory or defaults to USD **only for synthetic fixtures**; currency conversion is not implemented. Amounts are `Decimal` (not floats), and decimal strings are normalized. Public models: `OrderEvent`, `FillEvent`, `PositionSnapshot`, `CashMovement`, `FeeEvent`, `AccountSnapshot`, `PnLSnapshot`, `DrawdownSnapshot`, `SystemHealth`, `DataFreshness`. Order statuses: `new`, `accepted`, `partially_filled`, `filled`, `canceled`, `rejected`, `expired`.

- `reconcile(recorded_fills, broker_fills, recorded_positions, broker_positions, recorded_account, broker_account, *, broker_snapshot_complete=False, tolerance=Decimal('0.00000001')) -> ReconciliationResult`. Detects missing, mismatched and duplicate fill IDs, position discrepancies, and account discrepancies. `verified` requires no issues **and** caller attestation of complete, time-aligned broker snapshots; no actual broker data are fetched or attested by this package. **This API currently does not reconcile order lifecycle histories, individual fee events, or cash-movement IDs.** Those remain future work.
- `report(account, positions, fees, cash_movements, reconciliation, *, peak_equity=None, fees_complete=False) -> PortfolioReport`. `status` is `RECONCILED` or `UNVERIFIED`. Realized P&L must be supplied by a trustworthy accounting source **before fees**; no unsupported cost basis is invented. Net realized P&L is `None` unless reconciliation is verified **and** caller attests complete fee coverage for the **same period**. `total_observed_fees` counts `FeeEvent` only; do not duplicate `FillEvent.fee` as another `FeeEvent`. Missing unrealized P&L propagates as `None`. `raw_equity_drawdown_pct` is a simple peak-to-current snapshot drawdown, **not** flow-adjusted performance.
- `freshness(DataFreshness, now=UTC_datetime)` returns `FRESH`, `STALE`, `UNKNOWN`, or `CLOCK_SKEW`; feed-specific thresholds must be supplied externally and calendar/market-hours awareness is not implemented.
- `audit_metadata(meta, kind, status)` emits **only** safe allowlisted fields, hashed event ID and normalized source. Unstructured exceptions / sensitive payloads must never be passed to logs or Discord.

## Acceptance / verification

1. Package imports without Alpaca, Supabase, Discord or network credentials; only standard Python library is required. `python -m compileall quant_observability` succeeds.
2. Synthetic tests cover matching/missing/conflicting/duplicate fills, missing positions/accounts, incomplete broker snapshots, amounts with wrong currencies and invalid datetimes, missing P&L, non-confirmed fee completeness, cash flows, equity drawdown, stale/unknown feed timestamps, and safe audit metadata.
3. On disagreement, `reconcile().verified` must be false; reports must indicate unverified status. No broker transaction is performed.
4. No modifications to `quant_engine.py`, `live_paper_trader.py`, `dashboard_bridge.py`, `index.html`, `web/schema.sql`, `.env`, `.gitignore` or any deployed service.
5. Full repository test suite and external integrations are **not** verified by this isolated test run; existing test discovery and interfaces require independent owners.

## Integration sign-off needed from Master Architecture

A. Approve canonical immutable order/fill IDs and shared timestamp semantics with OMS and Risk.
B. Define broker snapshot completeness, source and time alignment; define fee coverage and settlement accounting for real P&L.
C. Agree on `quant_observability` v1 as the only read-only analytics contract and prohibit dashboards/Discord from accessing order authorization or broker writes.
D. Approve a separate authenticated read-only API and schema migration plan; no live data flows or Supabase writes until separate review and approval.
