# E1.14 — CI Evidence, Distributed Fencing and Broker Pagination

**Release decision: NO-GO (2 November 2026).** All changes are isolated to `fix/e1-safe-execution`. Do not enable execution, contact Alpaca, change Supabase, merge, or intentionally deploy.

## Evidence inventory

| Evidence | Observed result | Confidence |
| --- | --- | --- |
| E1.13 SHA `721ba677bbf5f501d6ef94b2e8cf06a5261135a0` combined GitHub commit status | Vercel success | Verified status only; **not pytest** |
| GitHub connector commit-associated Actions runs | Empty; endpoint filters PR-triggered runs, but workflow is push-triggered | **No CI conclusion** |
| Actions job IDs / job logs | Not obtainable without a run ID from the exposed connector | Unverified |
| Pytest JUnit XML artifact | Not retrievable without a run ID | Unverified |
| Disposable PostgreSQL logs | Not retrievable without a run ID | Unverified |
| Current E1.14 pytest and SQL results | Not run in this environment | **Unverified** |

No confirmed CI failure was available to diagnose. Do not mark the workflow green because Vercel is green. The existing workflow retains `e1-pytest-junit` and `postgres-contract-logs` artifacts; E1.14 adds `postgres-fencing.log` to the latter. Exact run URL, run ID, SHA, both job conclusions, JUnit test/failure/error counts, and SQL log contents must be independently captured before the gate can advance.

Review runs: https://github.com/LuckyCharmNoRAGRATS/trading-sandbox/actions/workflows/e1-isolated-tests.yml

## Distributed fencing: design only

The existing E1.3.1 database claim RPC uses an advisory transaction lock and a single unresolved-order global barrier. The Python worker uses claim-token compare-and-swap on `commands`. However, **it does not use a monotonically increasing fencing epoch**, and its local SQLite audit file is not a distributed lock.

E1.14 adds **draft only** `migrations/DRAFT_e1_14_fencing.sql`:

- A nonnegative `fence_epoch` column.
- `e114_claim_next_trade_command(uuid)` prototype atomically claims one pending command and increments its epoch; any unresolved command blocks claims.
- `e114_fenced_transition(id,token,epoch,expected,next)` prototype performs a database CAS requiring matching token, epoch and expected status. Allowed transitions are limited to `claimed -> submitting/failed`, `submitting -> acknowledged/uncertain`, and `uncertain -> uncertain`.
- Role permissions are restricted to `service_role`.
- Disposable PostgreSQL contract tests cover wrong token, stale epoch, duplicate transition, uncertain quarantine, valid claim identity and global unresolved exclusion.

**This SQL is NOT applied, and the Python worker is NOT switched to these RPCs.** No recovery or time-based lease stealing is implemented. Even a valid epoch CAS cannot atomically prevent an old process from calling Alpaca after losing its lease; broker-side idempotency and an authoritative single submission intent are still required. Never assume exactly-once broker delivery based on database tests.

## Broker open-order completeness and pagination

The pinned `alpaca-py==0.44.0` worker currently calls `GetOrdersRequest(status=OPEN,limit=500,nested=False)`. The API's page limit is 500; the SDK request lacks an order-ID cursor. Timestamp-only pagination cannot safely resolve identical timestamps or in-flight changes. Therefore **a 500-order page must continue to fail closed**. Do not implement offset, timestamp or fabricated ID-cursor pagination without a proven documented stable cursor and adversarial boundary tests.

Existing E1.13 tests exercise the 500-order boundary, malformed and duplicate order IDs, two-read drift, and mutable broker response reuse. This is conservative *completeness detection*, not full pagination. A real API/SDK cursor investigation is still required before supporting >499 open orders.

## Remaining release blockers

1. Obtain independently verified Actions run IDs, logs, JUnit and PostgreSQL conclusions for the **exact** E1.14 SHA.
2. Execute and pass the E1.14 disposable PostgreSQL fencing contract, including concurrent processes and crash/restart scenarios.
3. Integrate fencing only after an approved database migration plan and independent review; the existing worker must not run mixed old/new claim protocols.
4. Prove broker-side idempotency and reconciliation across stale-worker submissions, timeouts, partial fills, and a lost database acknowledgment.
5. Provide a broker-consistent order/position reservation model; two matching API reads are not atomic.
6. Validate complete open-order pagination beyond the maximum page or continue quarantining these accounts.
7. Add external immutable audit checkpoints, restoration exercises, secret/permission review, and separate human paper-only release approval.

**Operational state:** `E1_EXECUTION_ENABLED=false`; no Supabase changes, no Alpaca contact, no merge, no intentional deployment. NO-GO remains mandatory.
