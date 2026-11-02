# E1.19 — Offline Two-Reviewer Pelosi Adjudication

**Release decision: NO-GO.** No original House filing PDFs were supplied for the 72 quarantined tasks. No filing authenticity, disputed action, option exercise, or duplicate is certified. No data ingestion, paper/live order submission, network calls, scheduler activation, merging, or intentional deployment is authorized.

## Source and branch

- Repo: `LuckyCharmNoRAGRATS/trading-sandbox`
- Branch: `feature/e116-congress-readiness`, isolated from `main` and `fix/e1-safe-execution`.
- User-supplied Tracefour JSON SHA-256: `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`
- E1.18 input package: `pelosi_e118_manifest.json`, `pelosi_e118_human_review_queue.jsonl`, `pelosi_e118_semantic_duplicate_groups.csv`, `pelosi_e118_research_only.jsonl`. The E1.19 loader checks each file against the E1.18 manifest SHA-256 before use, validates all 72 task IDs and row/raw-record hashes, and refuses any pre-existing ingestion/trading promotion.
- Provider attribution: https://tracefour.com/trackers/pelosi (Tracefour compilation: CC BY 4.0). This is source metadata only; the original House PDFs are **not** being fetched.

## Current observed result

| Measure | Value |
| --- | ---: |
| Source records | 147 |
| E1.17 structurally clean, still unverified | 75 |
| E1.18 quarantined tasks awaiting two reviewers | 72 |
| Potential semantic duplicate groups | 12 |
| Uploaded and matched original House PDFs for E1.19 | **0** |
| Reviewer evidence events | **0** |
| Auto-reclassified / auto-deduplicated / promoted | **0** |
| Verified trading signals | **0** |
| Supabase writes / broker orders / external fetches | **0** |

The E1.19 output uses status `awaiting_two_reviewers` for all 72, and the audit ledger is intentionally empty. Its genesis and head hash are equal, which is the correct deterministic zero-event state.

## What the new module actually enforces

`congress_adjudication.py` is a strictly offline, explicitly opt-in adjudication workflow:

1. **Fail-closed lineage:** verify the E1.18 manifest, checked output hashes, row count, raw-record SHA-256, queue identity, and frozen `first_seen_at=null`, `official_filing_verified=false`, `eligible_for_ingestion=false`, `eligible_for_execution=false`.
2. **One local PDF per review:** the user supplies an actual `.pdf` file, a matching SHA-256 digest, 1-based PDF page number, and a 20–400 character verbatim transaction excerpt. Reject files that are missing, symlinked, oversized, not PDF, encrypted, without extractable page text, or with incorrect hashes/pages/excerpts. A matched excerpt must also contain the task's ticker and transaction date. No OCR, downloading, or URL following occurs.
3. **Two independent labels:** each task accepts a `primary` and `secondary` reviewer with distinct reviewer IDs (case-insensitive). The reviewer must separately categorize `action_verdict`, `asset_verdict`, `duplicate_verdict` using a narrow vocabulary. Same role/ID, unknown task, conflicting datum or unsafe verdict fails closed.
4. **No self-authorization:** matching reviews are labeled `two_reviewers_concordant_offline_only`; disagreements become `reviewer_disagreement`. Even matching reviews **never** establish government-file authenticity, retroactive filing availability, justified deduplication, ingestion permission, or any trading recommendation. All 12 duplicate groups remain review dependent.
5. **Deterministic ledger:** accepted review evidence events are sorted by source row then reviewer role, linked with SHA-256 `previous_hash` and `event_hash`, anchored to a genesis derived from the E1.18 manifest bytes and snapshot fingerprint. Tampering with ledger records or links fails verification; same exact inputs produce identical outputs. The head is **not externally notarized**; a determined adversary could replace an entire ledger and manifest. Reviewer names are ordinary user-entered labels, **not authenticated identities or cryptographic signatures**.

### Review inputs

The generated template `pelosi_e119_two_reviewer_template.csv` contains **144 blank slots** (72 tasks × primary/secondary). Never populate it with fabricated evidence.

When the user later supplies matching House PDFs and two genuine human decisions, prepare a local JSON file with the following structure. These are placeholders, **not performed reviews**:

```json
{
  "reviews": [{
    "task_id": "<existing-e118-task-id>",
    "row_index": 9,
    "role": "primary",
    "reviewer_id": "reviewer_01",
    "reviewed_at_utc": "2026-11-02T09:00:00Z",
    "pdf_filename": "user_supplied_house_filing.pdf",
    "pdf_sha256": "<actual-lowercase-sha256-of-pdf>",
    "page_number": 1,
    "transaction_excerpt": "<20-400 characters copied from the named PDF page, including ticker and trade date>",
    "action_verdict": "unknown",
    "asset_verdict": "unknown",
    "duplicate_verdict": "unknown",
    "notes": "Independently checked original document; unresolved categories remain unknown."
  }]
}
```

Supported `action_verdict`: `buy`, `sell`, `exchange`, `other`, `unknown`; `asset_verdict`: `stock`, `option`, `exercise`, `other`, `unknown`; `duplicate_verdict`: `distinct_transaction`, `same_transaction`, `amendment`, `unknown`. The second decision uses `role: secondary` and a different reviewer ID. Both entries may reference the same PDF only after separate review.

### Reproduce locally

From the checked-out branch, after installing pinned offline parser `pypdf==5.9.0`:

```bash
python congress_adjudication.py /path/to/e118_outputs --output-dir /path/to/e119_outputs
```

The above needs no PDFs and creates a fully pending queue. **Only after the user supplies original PDFs**, use:

```bash
python congress_adjudication.py /path/to/e118_outputs \
  --reviews-json /path/to/reviews.json \
  --pdf-dir /path/to/supplied_pdfs \
  --output-dir /path/to/e119_outputs
```

Outputs: status CSV, two-reviewer template CSV, hashed audit JSONL, SHA-256 output manifest. The record-level outputs explicitly forbid ingestion/execution. No automated status promotion, overwrite of E1.17/E1.18 source records, or Supabase write path exists in the new module.

## Executed verification and scope

Local test suite (Python + pinned dependency versions available in the sandbox):

```bash
PYTHONPATH=. E119_E118_DIR=/mnt/data PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q -p no:cacheprovider tests/test_e119_adjudication.py
```

**26 passed, 0 failed**, including the exact uploaded 72-task dataset regression, synthetic PDF tests, deliberate hash mismatches, invalid page or transaction quote, same reviewer identity, reviewer disagreements, cross-record tamper, deterministic manifest/ledger, CSV template blanks, and blocked network access.

The repository workflow has been extended to run the E1.19 suite with the existing socket guard and JUnit artifact; it has no cron, ingestion step, credentials, broker calls, or manual sync trigger. The private uploaded dataset is **not committed** and the private-data test is skipped in public GitHub CI. A GitHub Actions pytest job result has **not** been independently confirmed; a Vercel check is not pytest evidence.

## Strict limits and next action

- A matching SHA and quoted text establish **only consistency with the local PDF bytes**, not that the file genuinely came from the House clerk. Independent origin verification would require a separately authorized source process.
- A scanned PDF with no extractable text stays unresolved; this workflow deliberately performs no OCR.
- Source records missing provenance and records potentially describing the same transaction remain quarantined until reviewers supply actual, traceable evidence.
- Original system `first_seen_at` remains unknown, so historical trading backtests cannot assume filing-date availability.
- No E1.19 evidence outcome can authorize orders or a first Supabase sync. Those require separate milestones and explicit user approval.

**Next:** Request relevant original House PDFs from the user when available; then apply independent evidence review using this two-reviewer interface. Do not fetch the PDFs, contact external APIs, write Supabase, trade, merge, or deploy without separate authorization.
