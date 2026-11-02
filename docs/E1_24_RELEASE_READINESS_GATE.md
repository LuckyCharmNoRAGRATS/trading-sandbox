# E1.24 — Evidence-gated research release readiness (offline only)

**Release decision: NO-GO.** This milestone *does not* authenticate an original House filing, fetch data, write a database, schedule ingestion, place broker orders, merge branches or deploy an app. No external HTTP requests occur. It is not a live API connector.

## Motivation

E1.23 conservatively separates an already-supplied Pelosi/Tracefour JSON snapshot into research and quarantine partitions. A structurally clean provider row is **not** a verified House disclosure and not a point-in-time trading signal. E1.24 builds a separate, read-only, zero-write **release gate** before any future sync or execution discussion.

`congress_release_gate.py` accepts a **completed local E1.23 output directory** (`e123_manifest.json`, E1.17–E1.20 subdirectories). It refuses files with unexpected permissions/paths or incomplete/mismatched hashes. It checks:

- E1.23 file hashes, all nested E1.17–E1.20 artifact hashes, the source snapshot fingerprint, source row hashes, row identity and 147-row conservation;
- E1.18 correspondence to E1.17 for transaction identity, reported action/type, dates, source URLs, raw-record hashes, quarantine status and reason codes;
- E1.19 reviewer audit ledger remains empty and all 72 tasks are still pending. **A reviewer name or uploaded PDF path is not an authentication system**;
- E1.20's document request inventory remains unresolved. The 21 missing source links cannot be backfilled by unrelated filings;
- all `eligible_for_ingestion=false`, `eligible_for_execution=false`, `first_seen_at=null` and `official_filing_verified=false` claims are conserved.

The dry-run output is a **HOLD-only preview** with no SQL or Supabase SDK connection. SHA-256 hashes verify consistency with locally supplied bytes, not the authenticity of Tracefour or House filings; a malicious actor able to replace all files and hash manifests can also replace the entire bundle.

## Exact archived Pelosi snapshot result

Source: previously uploaded `tracefour_pelosi.json`, SHA-256 `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`. This is **not** a fresh Tracefour request. The user's independently reported HTTP 200 in an earlier conversation shows connectivity **from their Windows computer only**.

| Count | E1.24 observation |
|---|---:|
| Total records conserved | 147 |
| Structurally clean, source unverified | 75 |
| Quarantined awaiting human review | 72 |
| Suspected semantic duplicate groups | 12 |
| Documents requested for review | 17 |
| Rows with missing filing URLs | 21 |
| Human reviewer decisions | 0 |
| Original House PDFs independently authenticated | 0 |
| Rows authorized for ingestion | **0** |
| Rows authorized for trading | **0** |

The 147 row-level records are never silently dropped. `first_seen_at` is still unknown; the provider `ingestedAt`, transaction date and disclosure date must not be substituted for real first-observed history.

## Local execution

From repository root, with E1.23 outputs produced **offline** and their manifest present:

```bash
python congress_release_gate.py /path/to/e123_outputs --output-dir /path/to/new_e124_outputs
```

The destination must not already exist; the script atomically writes three local artifacts:

- `pelosi_e124_hold_queue.csv`: human-readable conservative HOLD disposition for every original row;
- `pelosi_e124_hold_queue.jsonl`: full hold status/provenance references for offline processing;
- `pelosi_e124_release_readiness.json`: deterministic count and SHA-256 manifest linked to the E1.23 output bundle.

Run offline tests with no-network guard:

```bash
PYTHONPATH=tests:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  -p e116_network_guard -p no:cacheprovider -q tests/test_e124_release_gate.py
```

Synthetic CI tests do not require private Pelosi rows; the optional exact-snapshot regression runs locally with `E124_E123_PACKAGE_DIR=/path/to/e123_outputs`. No network code or login credentials are required by this module.

## Verification performed

On the saved local E1.23 package from the prior milestone, with a socket-blocking pytest plugin:

- **E1.24 suite:** 17 passed, 0 failed (16 synthetic plus 1 exact archived Pelosi regression).
- **Broader available suites E1.17/E1.19–E1.24:** 154 passed, 0 failed, 3 skipped (the skipped tests require private data or an absent module in the isolated workspace). This broad run does not substitute for GitHub Actions and did not run E1.18 tests in this local sandbox.
- Deterministic reports compared byte-for-byte across two runs; negative tests checked modified action labels with recomputed manifests, source-row tampering, E1.18 duplicate rows, E1.19 fake approvals and URL/path and symlink hazards.
- **CI on GitHub has not been independently confirmed.** Vercel preview status, if any, is not evidence that Python tests succeeded.

## Required independent gates before future syncing

1. Independently authenticate the original House disclosure files; user-supplied PDFs may be screened offline, but their source authenticity is not established merely by having a valid PDF header or SHA-256 hash.
2. Complete two independent human reviews and resolve the 12 suspected duplicate groups and disputed `Other`/derivative classifications. Do not auto-promote records.
3. Establish a demonstrable first-observation timestamp for any future research feed, rather than backdating historical disclosures.
4. Review legal/license attribution, service usage terms and provider completeness, and establish a transactional database write process with independent rollback/health monitoring.
5. Obtain **separate explicit approval** before fetching external House PDFs, writing Supabase, enabling scheduled requests, merging, deploying or trading.

Until then, **NO-GO for any automatic sync or trading execution**; research-only local exports are allowed.
