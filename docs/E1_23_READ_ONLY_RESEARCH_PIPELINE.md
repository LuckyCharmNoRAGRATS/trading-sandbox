# E1.23 — Offline Tracefour Quarantine-Aware Research Pipeline

**Release decision: NO-GO.** This milestone does not authenticate original House filings, enable an API poller, schedule recurring fetches, insert Supabase rows, issue broker orders, approve research as trading signals, merge branches, or deploy software.

**Repository:** `LuckyCharmNoRAGRATS/trading-sandbox`; isolated branch `feature/e116-congress-readiness`.

## What was actually observed

A user-executed Windows PowerShell command `py congress_manual_probe.py --one-get --i-approve-one-get` returned a **real HTTP 200 JSON response**, `live_response_verified=true`, and 147 provider rows, including 21 missing HTTPS filing links. The command reported `response_sha256=8a83e7395115d3e7d9e99c3bed934d62a2c80bd981a17200824e4555d659ffd7` (86,605 response bytes), `meta.fetchedAt=2026-11-02T11:05:23.658Z`, and no database writes or trades.

**E1.23 made NO live requests.** The file available for our regression analysis, `tracefour_pelosi.json`, is an **earlier different snapshot** with SHA-256 `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`, 86,605 bytes, `meta.fetchedAt=2026-11-02T02:53:54.620Z`. Do not treat its data as the newer live response or invent observation timestamps.

Provider attribution for the earlier user-supplied snapshot: [Tracefour](https://tracefour.com/trackers/pelosi); compilation [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). User-provided provider rows have not been independently checked against the original House PDFs.

## Architecture and safety boundary

```
ALREADY-SAVED JSON (no URLs and no network)
       |
       +-- E1.21 diagnose_saved_json: provenance + freshness report
       |
       +-- E1.17 remediate_snapshot: 100% preserved source rows
       |         |-- structurally clean (source unverified, not ingestion-ready)
       |         +-- quarantined (missing PDF, Other/action ambiguity,
       |                          derivative mismatch, possible duplicates, etc.)
       |
       +-- E1.18 reconcile_e117: hash-checked review queue / group list
       |
       +-- E1.19 adjudicate(..., decisions=[]): ALL tasks pending
       |
       +-- E1.20 build_request_plan(..., pdf_directory=None): no retrievals
       |
       +-- E1.23 local manifest: immutable SHA-256 lineage + NO-GO flags
```

E1.22's opt-in live probe is **intentionally NOT imported or invoked**. Every record remains `eligible_for_ingestion=false`, `eligible_for_execution=false`, `official_filing_verified=false`, and `first_seen_at=null`. Apparent stock-option purchases remain derivatives, spouse ownership is not reattributed to Nancy Pelosi, and possible duplicate pairs are not deleted or merged.

**Fail closed:** require a bounded local regular non-symlink source file, member identity, `meta.count=1` (member count, **not trade count**), nonempty provider source, timezone-aware cache timestamp with at most five-minute future clock skew, full attribution and license, fewer than 500 records, consistent row hashes, exact row conservation, and blocked release permissions. The source may be stale **for historical offline research only**; the report explicitly marks its freshness problem and cannot authorize ingestion. Temporary local output directories are deleted on error, and an existing output path is never overwritten. All output hashes are included in the E1.23 manifest.

### E1.18 / E1.19 manifest interoperability fix

The repository's E1.18 generator currently writes the descriptive schema (`total_source_rows`, `source_snapshot_sha256`, `local_output_sha256`, etc.). E1.19/E1.20 originally expected an older short schema (`source_rows`, `source_sha256`, `output_sha256`). **Before E1.23, a fully generated E1.18 output could fail on entry to E1.19/E1.20.**

E1.23 adds `_compatible_e118_manifest` to `congress_adjudication.py`, which E1.20 already imports through `load_e118`. It accepts either schema, derives aliases **in memory only**, fails on contradictory alias values, refuses promotion/authentication claims, and verifies the original file checksums before any review. It **does not rewrite the original E1.18 manifest**, so original bytes remain available for the E1.19 ledger genesis hash. Tests cover both versions, malicious aliases, missing evidence, and the original no-review boundary.

## How to run the pipeline from the repository

With the saved JSON file already present on disk, and dependencies `pypdf`, `requests` installed:

```powershell
# Run from the checked-out feature/e116-congress-readiness branch.
# Store local snapshots/outputs under data/, which is already git-ignored.
py congress_research_pipeline.py data/tracefour_pelosi.json --output-dir data/pelosi_e123_research
```

**This command never contacts the API.** It writes only local research files: `e117/`, `e118/`, `e119/`, `e120/`, and `e123_manifest.json`. Repeated runs must use a *new* output directory; the pipeline refuses overwrites. CLI exit 0 means **an offline NO-GO research artifact was successfully produced**, never ingestion readiness. Review original filings separately, with explicit user authorization, before any adjudication.

## Offline results and test evidence

Using the older, uploaded snapshot with a fixed test assessment clock `2026-11-02T10:06:40Z`:

| Observation | Count |
| --- | ---: |
| Original records | 147 |
| Structurally clean (source unverified) | 75 |
| Quarantined | 72 |
| Missing/untrusted filing links | 21 |
| Ambiguous `Other` actions | 34 |
| Stock-description vs derivative/exercise disagreements | 10 |
| Potential semantic duplicate groups | 12 |
| Tasks awaiting two reviewers | 72 |
| Authentic House PDFs checked | 0 |
| API calls, Supabase writes, orders, scheduled jobs | **0** |

**Executed local pytest suite** for E1.17, E1.19, E1.20, E1.21, E1.22, and the E1.23 integration/bridge tests: **137 passed, 0 failed, 3 skipped** in 0.54 s with the socket-blocking plugin enabled. The three skips were optional tests dependent on additional private data or unmapped E1.18 source code in this isolated runner. E1.18's existing regression suite was **not rerun locally**; the current repository CI workflow includes it. In a separate local no-network check, the actual E1.19 and E1.20 modules successfully consumed the earlier archived E1.18 manifest: all 72 reviewer tasks pending, 17 document requests, 21 manual-link cases, 12 duplicate groups. An E1.23 full package replay reused the archived E1.18 output via a **test adapter**; this is not a claim of a fresh full E1.18 generator run. GitHub Actions results are only verified after a specific run/job is independently inspected.

Reproduce the local E1.23 subset without user data:

```bash
PYTHONPATH=tests:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  -p e116_network_guard -p no:cacheprovider -q \
  tests/test_e123_research_pipeline.py tests/test_e123_manifest_bridge.py
```

The 147-row snapshot test runs only when `E123_SNAPSHOT_PATH` is explicitly set to that local file. Do **not** commit source snapshots, data partitions, or derived human-review files to GitHub.

## Remaining blocks

- The user-confirmed HTTP 200 is from the user's Windows computer; **no continuously running poller** is approved or configured. E1.23 is deliberately file-based.
- 21 missing source filing links and additional overlapping anomalies remain quarantined; no missing link can be inherited from another row as authenticated provenance.
- Matching URL form is not authenticating House PDF contents; no original PDFs were supplied for independent review.
- Historical `first_seen_at` cannot be reconstructed from transaction, filing, upstream ingestion, or provider cache timestamps.
- No Supabase import, scheduler activation, execution, merged branch, or explicit deployment is authorized.
- GitHub pushes on this branch have previously triggered automatic Vercel previews. Record any incidental deployment separately and do not infer backend activity from a dashboard preview.
