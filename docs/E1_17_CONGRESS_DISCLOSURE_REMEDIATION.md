# E1.17 — Offline Pelosi Disclosure Quarantine & Research Dataset

**Decision: NO-GO for Supabase ingestion, order execution, scheduling, merging, and deployment.** This work only analyses the user-supplied response already saved as `tracefour_pelosi.json`; it does not call Tracefour, fetch House PDFs, or modify remote services.

## Snapshot provenance and limits

- Snapshot SHA-256: `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`; 86,605 bytes, 147 rows.
- Tracefour member endpoint: `https://tracefour.com/v1/congress/nancy-pelosi`; supplied `meta.fetchedAt=2026-11-02T02:53:54.620Z`.
- Attribution: https://tracefour.com/trackers/pelosi — compilation under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Links to *reported* original filing PDFs are carried per record.
- This is a **user-supplied file, not an independently authenticated API download**. Neither actual PDF content, complete historical coverage, nor transaction accuracy has been verified.
- `meta.count=1` counts member records, **not** the 147 transactions.
- **DO NOT** backfill `first_seen_at` from `transactionDate`, `disclosureDate`, `ingestedAt`, or `meta.fetchedAt`. It remains `null` in every exported row; historical as-of trading simulation requires independent observation history.

## Quarantine policy

No row is silently corrected, reclassified, deduplicated, or converted into a buy/sell signal. Both sides of a potential semantic duplicate are quarantined. The clean partition is only **structurally clean but filing-unverified**. Correctly reported stock options may remain in the structurally clean research partition *as options*, with execution and ingestion flags set to false.

| E1.17 disposition | Count |
| --- | ---: |
| All source rows preserved across both partitions | **147** |
| Structurally clean, source-unverified research records | **75** |
| Quarantined for filing-level/manual review | **72** |
| Missing or untrusted original filing URL | 21 |
| Reported `Other` actions | 34 |
| `Other` whose filing ID text suggests a sale | 34 |
| `Stock` label with derivative/exercise language in description | 10 |
| Possible semantic-duplicate records | 24 in 12 groups |
| Missing or timezone-naive upstream timestamps | 21 |

Reason counts overlap: do **not** sum them to calculate quarantined records. Semantic groups are flagged by ticker, transaction date, reported action, owner, amount bracket and reported asset type; matching keys do **not** prove the transactions are duplicates. The source provider's `filingId` is kept unchanged. Unverified options, exercises, sale interpretations and source URLs require filing-level review before promotion.

## Outputs (not checked into GitHub)

Run locally on **an existing saved file only**:

```bash
python congress_remediation.py /path/to/tracefour_pelosi.json --output-dir /path/to/e117-output
```

The command writes locally:

- `pelosi_e117_clean_research.csv` and `.jsonl` — 75 structurally clean *unverified* research rows.
- `pelosi_e117_quarantined_review.csv` and `.jsonl` — 72 quarantined rows with explicit reason codes and semantic group identifiers.
- `pelosi_e117_summary.json` — source file SHA, record and quarantine counts, attribution/license, output hashes, and NO-GO status.

The JSONL preserves the **full original raw record** plus `raw_record_sha256`, original source row number, source filing ID and URL, attribution, owner, dates, raw upstream timestamp, provider-cache timestamp and input-file SHA. The CSV is an Excel-safe convenience view without the raw embedded JSON. Every record has `first_seen_at=null`, `eligible_for_execution=false`, `eligible_for_ingestion=false`, `official_filing_verified=false`. Exports are saved as local user artifacts, not committed copies of the unverified provider snapshot.

## Executed offline verification

```bash
E117_SNAPSHOT_PATH=/absolute/path/tracefour_pelosi.json \
PYTHONPATH=tests:. python -m pytest -p e116_network_guard -p no:cacheprovider -q \
  --junitxml=/path/pelosi_e117_junit.xml tests/test_e117_remediation.py
```

**23 tests passed** in **0.11 seconds** for the exact user snapshot SHA and synthetic cases. Validated zero-drop partition, expected 75/72 split, link omissions, ambiguous source sale labels, call/exercise mislabeling, semantic duplicate groups, naive timestamps, invalid dates/amounts, CSV formula injection, output digest determinism, and no trade/ingest authorization. The optional real-file test is skipped in ordinary CI unless `E117_SNAPSHOT_PATH` is supplied. Prior E1.16 tests were not rerun in this local invocation, and there is no independently retrieved GitHub Actions run result.

## Next release blockers

1. Reconcile quarantined rows against original House PDFs with **separately approved read-only retrieval**, or human-provided source PDFs. Never fabricate a missing URL.
2. Review potential semantic duplicates as distinct filings or amendments; don't discard records based on a heuristic.
3. Resolve mislabeled options/exercises and `Other` sale interpretations only with evidence from the originals.
4. Establish authentic provider history and the system's first observation timestamps before any backtest. A provider's historical `ingestedAt` is not a substitute.
5. Retest after any refinement and obtain independently verified CI run IDs and artifacts.
6. Obtain **separate explicit approval** before any Supabase writes, scheduler activation, merges, deployment or trading.
