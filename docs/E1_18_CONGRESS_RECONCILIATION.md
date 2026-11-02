# E1.18 — Offline Pelosi Filing Reconciliation Queue

**Readiness: NO-GO. No source filings independently inspected. No claims of true duplicates, corrected actions, verified holdings, investment returns, or executable signals.**

This milestone uses the **user-uploaded** Tracefour member response (`tracefour_pelosi.json`) and previously generated E1.17 `.jsonl` partitions and SHA-256 manifest. No provider endpoints or House PDFs were downloaded or opened, no Supabase rows/schema were changed, and no paper/live orders, merges, intentionally deployed apps, or schedules were activated. The relevant feature branch is `feature/e116-congress-readiness`.

## Source lineage

- Provider: Tracefour member disclosure response for Nancy Pelosi, originally obtained by the user.
- File fingerprint: `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3` (SHA-256 of the uploaded JSON bytes).
- Provider cache timestamp: `2026-11-02T02:53:54.620Z`; *not* a historical trade-publication timestamp, and *not* a trustworthy first-observed-at timestamp for old trades.
- Provider attribution link: https://tracefour.com/trackers/pelosi. Compilation CC BY 4.0: https://creativecommons.org/licenses/by/4.0/.
- E1.17 outputs are validated against their manifest SHA-256 hashes. Every original row is checked against its preserved `raw_record_sha256`. A hash identifies bytes and detects accidental tampering; it **does not authenticate the original website or an original House PDF**.
- Source snapshot has 147 rows; 75 were structurally clean but source unverified, and 72 were quarantined. E1.18 preserves these partitions without automatic promotions.

## Reconciliation summary (offline evidence only)

| Review measurement | Finding | Interpretation |
|---|---:|---|
| Source rows | 147 | All retained in E1.17 lineage |
| Structurally clean research-only rows | 75 | Still **not verified** against government filings |
| Quarantined row-level review tasks | 72 | Pending original-filing evidence |
| Possible duplicate groups | 12 | 24 member rows; none merged, deleted or resolved |
| Missing/untrusted original PDF links | 21 | Need independent official link identification |
| `type="Other"` and sale-like source filing identifier | 34 | Sale is a **tentative interpretation**, never an accepted action |
| Reported `Stock` with option/call/exercise language | 10 | Asset/economic-event type requires human source review |
| Upstream timestamps missing an explicit timezone | 21 | No assumed UTC conversion |
| Human-review priority P1 | 44 | Ambiguous action, derivative/exercise or other elevated risks |
| Human-review priority P2 | 28 | Duplicate/link or other provenance issues |
| Official PDF bytes independently validated | **0** | Every review outcome remains pending |
| Automatic classification/deduplication | **0** | All raw fields preserved |

Reason counts overlap; the counts above **must not** be summed as independent records.

## How the human queue works

1. **Start with the 12 duplicate groups:** open `pelosi_e118_semantic_duplicate_groups.csv`. Check all listed official filings and the actual transaction line, noting whether the pair represents a true duplicate, amendment, repeated independent transaction, or an unresolved conflict. Different filing dates and different PDF URLs are *clues only*. The pipeline never picks a winning row.
2. **Review missing provenance:** for each missing or invalid `sourceLink`, independently obtain the matching original House disclosure. The `suggested_evidence_url_unverified` field may show a PDF link from a *related* record in the same suspicious group. **It is not a verified match and is never copied into `official_filing_url`.**
3. **Review ambiguous actions:** a `Sale (...)` substring embedded in a provider-generated filing identifier may suggest a sale for a row labeled `Other`; this is a review question, not grounds to change the stored action or create a sell signal.
4. **Review stock versus derivatives/exercises:** plain-text mentions of `call`, `option`, `LEAP` or `exercised` require distinguishing direct equity trades, options contracts, and option exercises. Do not automatically set `asset_type` to stock or option, invent strike/quantity, or backfill fills.
5. **Capture manual decisions in a separate template:** `pelosi_e118_review_decision_template.csv` has 72 blank rows keyed by `task_id`. If original PDFs are later user-supplied and separately reviewed, a reviewer can record a source PDF SHA-256, the exact PDF page/transaction evidence, notes, reviewer and second reviewer. Merely filling the template does **not** unquarantine or authorize ingestion.

Output fields distinguish the source's reported action/asset type from unverified interpretation proposals. All record-level outputs explicitly keep `first_seen_at=null`, `official_filing_verified=false`, `eligible_for_ingestion=false`, and `eligible_for_execution=false`.

## Offline reproduction

On `feature/e116-congress-readiness` with E1.17 `.jsonl` partitions and manifest in `E117_DIR`:

```bash
python congress_reconciliation.py "$E117_DIR" --output-dir "$OUTPUT_DIR"
```

The script verifies E1.17 output hashes and every source record hash before writing anything. It creates only **local files**: review queue CSV and JSONL, duplicate-group CSV, decision template CSV, unverified research JSONL, and a fingerprint manifest.

Offline suite (no user data is committed):

```bash
PYTHONPATH=tests:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  -p e116_network_guard -p no:cacheprovider -q --tb=short \
  --junitxml=test-reports/congress-e118-junit.xml \
  tests/test_congress_ingest.py tests/test_congress_data_layer.py \
  tests/test_tracefour_ingest.py tests/test_e115_congress_research_guard.py \
  tests/test_e116_quality.py tests/test_e116_offline_preview.py \
  tests/test_e117_remediation.py tests/test_e118_reconciliation.py
```

The test using the private uploaded dataset is optional and only runs if `E118_E117_OUTPUT_DIR` points to the real E1.17 outputs. The E1.17 test using raw user source similarly requires `E117_SNAPSHOT_PATH`. **Neither private input is committed or required by CI.**

### Executed local evidence

**139 passed in 0.18s, zero failures** with real input paths for both optional regression tests. The suite used the network guard. Files used locally match the source GitHub blob SHA for the previously committed E1.16/E1.17 files. GitHub Actions jobs/JUnit artifacts are **not independently verified**; Vercel status is not Python test evidence.

## Remaining hard blockers

- Original House PDFs were not retrieved or provided. Therefore there is **no evidentiary basis to resolve the 12 candidate duplicate groups or repair the 21 missing links**.
- Independently review and approve any future corrections against original documents; preserve original source rows and add a separate, auditable adjudication ledger. E1.18 does not implement an automatic status promotion function.
- Validate whether Tracefour omits historical filings, includes amendments, or conflates exercises and underlying shares. The reported 500-row cap is not proof of complete history.
- For historical backtesting, establish each record's first availability in a replayable archive rather than assuming it was known on transaction or filing date. The old records' `first_seen_at` remains unknown.
- Confirm full GitHub Actions test evidence before release, and request separate user permission before any external PDF/API access, Supabase changes, scheduled syncs, merges, intentional deployments, or broker orders.