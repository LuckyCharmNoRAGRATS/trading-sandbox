# E1.22 — Manual one-shot Tracefour connection check (NO-GO)

**State:** A safe opt-in connection test is prepared. **No new external GET was performed in E1.22.** The one previously approved E1.21 attempt returned a retrieval-system Cache miss, not a Tracefour HTTP response. That one-request approval has been used; it does **not** authorize a retry. Supabase writes, scheduled ingestion, trading, merges and intentional deployments remain prohibited.

## Problem and solution

E1.21 supported saved-file inspection and an injected-session `probe_once`, but it had no manual command for verifying reachability from the actual computer or backend that will run the software. E1.22 adds `congress_manual_probe.py`, a **manual-only** CLI with a fixed Tracefour member URL, explicit opt-in, zero configured HTTP retries, no redirects, connect/read timeouts, and a 2 MB response limit.

It has no Supabase, broker, cron, database writes, order routing, or arbitrary-URL flags. The live request is **not** invoked by GitHub Actions. No credentials are needed to inspect the public endpoint; the CLI does not save raw transaction data.

## Safe offline mode — can run immediately

From the repository root:

```bash
python congress_manual_probe.py --offline-file /path/to/tracefour_pelosi.json
```

The command reads local JSON only. Its cache freshness is assessed against the **actual current UTC time** and its report contains `http_status=null`, `transport_status=not_tested_by_saved_file`, and `outbound_http_requests=0`. A saved file cannot prove current backend network connectivity.

## Future network mode — only after NEW explicit user approval

**Do not execute this command under the consumed E1.21 approval.** After the user separately authorizes one new read-only HTTP GET **from the intended target computer or backend**, an operator can run:

```bash
python congress_manual_probe.py --one-get --i-approve-one-get
```

Both flags are required. Without both, the process exits before creating an HTTP session. This is a **single manual test**, not a schedule or standing authorization. The URL is hardcoded to `https://tracefour.com/v1/congress/nancy-pelosi`; `requests.Session` is configured with zero urllib3 retries; the probe explicitly sets `allow_redirects=False` and `stream=True` and bounds the response body. Proxies and lower-level networking may affect the destination but do not grant further permissions.

The report separates transport-level failures from source-validation problems. Exit codes: `0` means valid source JSON structure **only**; `2` means a blocked source or invalid CLI authorization; `3` means no HTTP response, HTTP error, redirect, invalid content type, or unreadable/oversized body. **Exit 0 does not authorize ingestion.** All reports preserve `approved_for_ingestion=false`, `approved_for_trading=false`, and `scheduled_sync_enabled=false`.

## Source-quality blockers still present

The saved user-uploaded snapshot has SHA-256 `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`, contains **147 rows**, and has **21 missing original filing URLs**. Its cache was already stale by the E1.21 observation time. A valid later HTTP response might still fail quality gates and must **not** be partially ingested.

The offline diagnostic also now safely rejects malformed URL syntax, noncanonical House PDF paths, alternate ports, userinfo, query strings and spoofed hosts. Matching a URL format does not independently authenticate a House PDF.

## Tests actually executed

Local isolated environment (user data included, no network):

```bash
cd /mnt/data/e121_connection_work
E121_SNAPSHOT_PATH=/mnt/data/tracefour_pelosi.json \
PYTHONPATH=tests:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p e116_network_guard -p no:cacheprovider -q \
  tests/test_e121_connection_diagnostic.py \
  tests/test_e122_manual_probe.py
```

**39 passed, 0 failed, 0 skipped** with the socket-network guard and exact saved snapshot. CI-style run without the private file: **38 passed, 1 intentionally skipped**. These tests simulate GET responses using fakes, but do **not** call the real API. GitHub Actions results are not verified until an actual Actions job conclusion and JUnit artifact are inspected; Vercel success is not Python-test evidence.

The broader E1.16–E1.20 suite was not reexecuted as part of these focused local tests. GitHub CI registers this test suite in its branch-scoped **offline-only** workflow with no cron, manual ingestion trigger, Supabase credentials or trading steps.

## Next approval boundary

Live connectivity must eventually be checked on the **actual intended backend host**, using a newly approved single read-only GET. A later Supabase data-only sync, recurring schedule, deployment, or broker operation each needs independent authorization. This branch remains **NO-GO**.
