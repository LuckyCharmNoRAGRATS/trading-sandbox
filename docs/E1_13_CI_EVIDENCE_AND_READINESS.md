# E1.13 — CI Evidence & Operational Readiness Assessment

**Decision: NO-GO.** Do not enable paper or live execution, apply Supabase migrations, merge, or deploy on the basis of this milestone.

## CI evidence status (2 November 2026)

Target: `LuckyCharmNoRAGRATS/trading-sandbox` / `fix/e1-safe-execution`.

- E1.12 commit: `06655f54bd850541ee4ca6511a9fb0c51f86e17f`.
- The connected GitHub combined-status endpoint returned **Vercel success only** for that commit.
- The connected commit-workflow-runs endpoint returned **no runs** because the wrapper filters for pull-request-triggered runs, while the E1 workflow triggers on branch pushes.
- Public GitHub Actions/API URLs were inaccessible from this environment. **No run IDs, jobs, full logs, JUnit XML, or PostgreSQL job artifacts could be independently retrieved.**
- The E1.13 workflow contains an opt-in `workflow_dispatch` trigger and remains restricted to `fix/e1-safe-execution`; triggering it has **not** been requested or performed here.
- **Result:** no verified green pytest or PostgreSQL result. A Vercel status is not CI evidence.

To establish independent evidence: open [E1 isolated GitHub Actions](https://github.com/LuckyCharmNoRAGRATS/trading-sandbox/actions/workflows/e1-isolated-tests.yml), run or select the workflow on `fix/e1-safe-execution`, verify both jobs, inspect archived JUnit/PostgreSQL logs, and record exact run ID, commit SHA, test counts, duration, and conclusion.

## E1.13 implementation

1. **Open-order completeness:** `broker_snapshot.py` requests `QueryOrderStatus.OPEN`, `limit=500`, `nested=False`. A response of 500 orders is **blocked**: the API may have more. Malformed, duplicate-ID, terminal, and nested/complex orders are rejected. This implementation **does not** paginate the >499-order case; it fails closed until ID-cursor pagination is verified for the pinned SDK/API.
2. **Sequential broker stability check:** the account, positions and open orders are obtained twice. Inconsistent equity, exposure, holdings, quantities, order IDs or statuses block the snapshot. Two matching reads are **not atomic** and cannot eliminate order/market changes between the final read and broker submission.
3. **Submission baseline gate:** only a snapshot marked with explicit open-order coverage and observational-stability checks can reach the pre-submit audit barrier. Mocked contract tests explicitly supply these proof flags.
4. **Local audit file disappearance:** an initialized audit store now refuses to transparently recreate a missing database file. Worker recovery requires an existing store. This protects against inadvertent audit loss within one process; a brand-new local SQLite database or complete hostile database rewrite still needs an external immutable anchor.
5. **Separate-process tests:** disposable subprocesses race to write a baseline for the same command; at most one succeeds. Additional subprocesses verify the global append-only hash chain. This does **not** prove distributed Supabase RPC/Alpaca broker exactly-once semantics.

Official Alpaca references: [Get All Orders](https://docs.alpaca.markets/us/reference/getallorders-1), [alpaca-py GetOrdersRequest](https://alpaca.markets/sdks/python/api_reference/trading/requests.html). The API's default limit is 50; max is 500.

## Open critical release blockers

- [ ] Obtain actual green E1.13 GitHub Actions **Offline E1 pytest** and **Disposable PostgreSQL claim tests** evidence, with reproducible run IDs and archives.
- [ ] Validate branch source changes locally or using the CI runner; static GitHub-source verification alone is not a passing test.
- [ ] Confirm SDK-compatible complete order-pagination behavior beyond 499 results; until then those accounts are quarantined.
- [ ] Eliminate the residual TOCTOU race between broker snapshot, database claim and final submission via broker/order reservation reconciliation.
- [ ] Independently validate Supabase claim ownership/fencing across host loss and at least two truly distributed workers.
- [ ] Add signed/WORM audit checkpoints, backup/restore drills and a fail-closed response to complete local audit database replacement.
- [ ] Verify protective exit orders, cancel/replace handling, partial fills, corporate actions, slippage and outage controls.
- [ ] Review import-time network side effects, dependency integrity, secret handling and separate human paper-trading sign-off.

## Allowed behavior / rollback

Remain in audit-only, test-only development mode. Leave `E1_EXECUTION_ENABLED=false`; no Alpaca calls or orders, no Supabase mutations, no merges, no intentional deployments. Keep acknowledged/uncertain commands unresolved and never auto-resubmit after failed broker response. An existing Vercel preview status cannot authorize any release.

**Next recommended milestone E1.14:** obtain independent CI job evidence, fix verified failures, then tackle end-to-end distributed fencing and broker snapshot atomicity risks—still without enabling order submission.
