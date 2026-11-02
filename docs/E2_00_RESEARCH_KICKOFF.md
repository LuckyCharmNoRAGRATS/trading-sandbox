# E2.0 — Quant research foundation (proposed roadmap; research only)

**Scope:** `LuckyCharmNoRAGRATS/trading-sandbox`, current isolated protected branch `feature/e116-congress-readiness`.

**Status:** E2 engineering may begin in parallel with unresolved E1 evidence review. This is **not** a release of the E1 Pelosi data, a live data feed, paper/live trading, database ingestion, or a deployed service. The existing GitHub repository did not contain a pre-defined E2 roadmap; the scope below is a newly proposed roadmap.

## E1 dependency contract

- A user-reported Windows HTTP 200 verified reachability of Tracefour and a 147-row JSON feed, but did not authenticate original House source filings or historical first-public-observation times.
- Source snapshot SHA-256: `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`.
- E1.23–E1.25: 147 preserved source records; 75 structurally clean but source-unverified, 72 human-review quarantined, 21 missing filing links, 17 distinct candidate House PDF documents, 12 unresolved possible duplicate groups, and 144 blank independent-reviewer slots.
- Neither provider `ingestedAt` nor the current time is a provable historical first-public-observation timestamp. An archived/old filing date is **not** a realistic backtest entry timestamp without point-in-time evidence.
- Every historical row remains HOLD; zero eligible real events, zero trading signals, no Supabase ingestion or paper/live trades permitted by E2.0.

## E2.0 deliverables

`congress_e2_research.py` contains exactly two intentionally constrained entry points:

1. `assess_real_source_hold(e123_dir)`: verifies the existing hash-linked E1.17→E1.23 artifact chain through `congress_release_gate.verify_research_package` and returns the explicit `NO_GO` real-event-study status, preserving held-record counts and lineage. The procedure cannot approve any record. It does not read an API, connect a database, or establish historical first-observation timestamps.
2. `synthetic_close_to_close_study(events, bars, asof, forward_bars=1)`: a bounded **synthetic-only** educational demonstration, whose event and market-bar inputs must all be marked `dataset_kind=synthetic_fixture_only`. It requires timezone-aware first-observation and data-availability timestamps, excludes option/ambiguous assets, rejects duplicate identities, missing futures, and bars unavailable by `asof`, and measures hypothetical price-change fractions between strictly post-observation bar closes. It never infers a tradable entry or sends an order. The synthetic tag is a test-mode assertion, **not** an authentication system.

The reference close must have been available before the forward bar, preventing a late historical correction from becoming a retroactive baseline. No true E1 Pelosi events are fed into this function.

## Proposed E2 development phases

| Stage | Safe offline scope | Gate before broader use |
| --- | --- | --- |
| **E2.0** | Event time, data availability, source lineage and synthetic point-in-time studies | Unit/integration tests; real Pelosi HOLD enforced |
| **E2.1** | Define auditable disclosure event schema, schema migrations *in dry-run only*, reviewer evidence prerequisites, symbol history | Authenticated House filings, real independent reviews, duplicate resolution, actual historical observation timestamps |
| **E2.2** | Backtest engine over licensed historical market-data snapshots; as-of retrieval, corporate actions and survivorship analysis | User approval for each external data source and relevant licenses; point-in-time provenance |
| **E2.3** | Cost-aware delayed-disclosure event studies, sensitivity checks, chronological out-of-sample evaluation, negative controls | Statistically credible results after leakage/overfitting/transaction-cost audits |
| **E2.4** | Isolated shadow-mode or paper-trading design, kill switches and continuous reconciliation | Separate explicit approval, verified source feed, operational readiness; no real orders by default |

Real-money trading and unattended scheduled fetching are **not** authorized by any E2 stage automatically. Even audited filings cannot establish an investing edge by themselves.

## Verification

- Offline tests run under the existing `e116_network_guard`. No web/HTTP, Supabase, broker or scheduler code is imported by the new E2.0 module.
- Targeted E2.0 tests: 26 passed with the local private E1.23 archived package; without that private archive the E1 test is an expected skip.
- Combined available local E1.17–E1.25 and E2.0 synthetic suites: **197 passed, 10 expected skips**, 0 failures. These counts do not include all GitHub E1.15/16, current E1.18 full generator or the complete E1.15/16 and current E1.18 generator suites, which require additional source/test files not present in the isolated local workspace. GitHub Actions results are a separate check.
- Tests cover absent/naive observation times, real provider data rejection, chronological signal leakage, late price revisions, wrong asset/ambiguous action, missing future bars, duplicate identities, malformed/future prices, and no order execution.

## Operating restrictions

- The code is **research-only** and offline. No source feed retrieval, source PDF retrieval, Supabase mutation, broker order, scheduler activation, merge or intentional deployment is part of E2.0.
- Any GitHub code commit remains on the existing protected branch, with Vercel research-branch automatic preview suppression preserved.
- E1 source authentication and manual reviews remain a necessary independent workstream; E2 synthetic math is not a workaround.
