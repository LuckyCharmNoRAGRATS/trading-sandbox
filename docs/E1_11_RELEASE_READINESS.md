# E1.11 — Execution Safety Release Checklist (NO RELEASE APPROVAL)

**Status: NOT APPROVED.** This document is a review gate, not permission to enable orders.

## Current implementation boundaries

- Paper client construction is coded with `paper=True`; no live-order authorization.
- E1.11 adds `E1_RELEASE_APPROVED=paper-only-reviewed`, `E1_BROKER_MODE=paper`, `E1_RECONCILIATION_ENABLED=true` and an existing absolute `E1_AUDIT_DB_PATH` as **necessary but insufficient** conditions.
- `E1_EXECUTION_ENABLED` must remain **false** during development and CI.
- The E1.3 SQL migration remains **unapplied**. No live Supabase schema or policy changes are authorized.
- The audit database is local SQLite and its hash chain has **no external independently signed checkpoints**.
- The order-reconciliation logic does **not** automatically release acknowledged commands.
- Alpaca portfolio reads are not atomic across account, position and open-order endpoints.
- There is no proven mechanism for comprehensive protective exit order coverage.
- `process_claimed_order` is an internal method, not a publicly authorized trading API. No execution activation permitted on these checks alone.

## Mandatory pre-release evidence

- [ ] Confirm a completed green **Offline E1 pytest** GitHub Actions run on the precise proposed commit; archive JUnit artifact.
- [ ] Confirm a green disposable **PostgreSQL claim tests** job on that commit; archive logs.
- [ ] Independently review draft SQL migration, RLS permissions, role grants, uniqueness and rollback.
- [ ] Demonstrate cross-process claim idempotency with 2+ independent workers under fault injection.
- [ ] Prove no duplicate orders after broker timeout, worker termination, lost DB acknowledgement, and restart.
- [ ] Implement complete/verified Alpaca open-order pagination and complete portfolio snapshots.
- [ ] Reconcile fill history to portfolio changes, including external activity and corporate actions.
- [ ] Provide immutable external audit checkpoints, disaster recovery and database backup/restore drills.
- [ ] Enforce protective exit placement/verification before releasing any risk-increasing trading capability.
- [ ] Perform controlled non-ordering staging validation with explicit approvals and documented operators.
- [ ] Confirm Python dependencies, isolated networking controls, secrets and runtime configuration.
- [ ] Review `.env` and deployment secrets to ensure no environment overrides execution disable.
- [ ] Obtain independent human sign-off for **paper-only** release. Live-money approval requires a separate, later process.

## Failure actions

**Any failed, missing or stale gate:** disable execution, preserve existing evidence, stop claiming new commands, review broker state manually, never automatically re-submit. Do not run rollback on unresolved trade commands.

## Explicit prohibitions during E1.11

Do not turn on `E1_EXECUTION_ENABLED`; do not run the worker against Alpaca; do not apply Supabase migrations; do not merge to main; do not promote or deploy. GitHub branch pushes may independently trigger Vercel previews, which should not be interpreted as release approval.
