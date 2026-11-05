# E1 test-baseline integration candidate

Base: execution safety commit 327265459d26f9c00cead83b6b24d8bb1e8c1105.
Recovered test baseline: bd003020e0237b0db670a4d6008822c61e7f9167.

This is a draft integration increment, not permission to trade or deploy.
The PR targets fix/e1-safe-execution, not main, to keep the review limited to
test integration rather than the entire existing execution branch.

## Changes

- Root pytest discovery covers tests; removes the nested stale configuration.
- Renames the extensionless execution-gate tests so pytest discovers them.
- Retains the execution branch's already corrected cache-interface tests.
- Ports manual initialization coverage to the claimed-command interface and
  verifies risk rejection before submission. Checks automated entries remain off.
- Preserves the legacy ExecutionOMS full-validation bypass as one strict xfail.
  This is an unresolved release blocker, not a passing safety guarantee.
- Stops stats_analyzer from fetching data at import; explicit CLI execution
  retains the diagnostic. A regression test prohibits import-time data loading.
- Keeps the data fixture safely inside its requested time window.
- The CI network guard disables dotenv loading and clears service credentials
  before test collection. Tests use mock clients, not broker or Supabase access.
- Adds full-discovery CI plus existing disposable PostgreSQL contract tests.
- Disables Vercel Git deployments for integration/e1-test-baseline.

## Local verification

Python 3.12: 264 passed, 1 strict expected failure, 3 pandas deprecation warnings.
Local dependency versions differ from repository pins; CI installs requirements.txt.
Five edited Python files parsed successfully. No application strategy or order
runtime source changes relative to the execution-safety base, except the separate
stats_analyzer diagnostic import fix. No migrations applied to production.

## Remaining gates

- Repair or retire the alternate ExecutionOMS entry path and turn its strict
  expected failure into a passing test before any release approval.
- GCP deployed revision, full bb_window traceback and polling remain unverified.
- November 4 broker history and P&L reconciliation remain unverified.
- Draft fencing SQL is not wired into the Python worker; SQL tests do not prove
  production distributed fencing or authorize applying the draft migration.
- Repository credential exposure and rotation status remain unresolved.
