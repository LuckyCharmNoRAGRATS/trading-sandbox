# E1.25 — Offline Pelosi evidence-review handoff (read-only, NO-GO)

**Scope:** `LuckyCharmNoRAGRATS/trading-sandbox`, isolated branch `feature/e116-congress-readiness`. This is an **offline handoff** of the archived E1.23/E1.24 research evidence; it is **not** source authentication, live Tracefour ingestion, database sync, trading permission, or an automated service. No House PDFs were retrieved. The previously reported Windows Tracefour HTTP 200 is not independently repeated here.

## E1.25 implementation

`congress_review_handoff.py` builds a deterministic, human-readable evidence review pack from a **complete local E1.23 output directory** (`e123_manifest.json`, stages E1.17–E1.20). It calls E1.24's strict `verify_research_package` **before preparing any output**. It additionally validates:

- E1.18 task IDs, source row index and raw-record SHA-256 against the E1.24 all-HOLD release decision;
- E1.19's blank review CSV has **exactly one primary and one secondary slot** per task, with no invented reviewer names, decisions, dates, page references or PDF SHA-256 values;
- E1.20's candidate document URLs correspond exactly to source-linked task IDs and row indices, including priority and action/derivative ambiguity counts. The URL is a **provider-reported location suggestion**, not proof of official House origin;
- Missing-filing-provenance tasks are conserved separately, not linked to another person's/document's filing or silently dropped;
- All suspected semantic duplicate groups have their original members and candidate document/missing-link status intact; no semantic deduplication occurs;
- Document authenticity, official filing verification, historical `first_seen_at`, ingestion approval and trading approval remain **false or unknown**.

The output directory **must not already exist**. Files are staged and published only after validation and are linked by SHA-256 in a local manifest. Hashes establish internal consistency of local bytes, not a government-origin signature or a trusted first-observation timestamp. The generated CSV fields escape spreadsheet formula prefixes.

## Archived Pelosi snapshot result

Baseline source JSON file SHA-256 `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`; use only the uploaded, offline snapshot. This is separate from the more recent **user-reported** live HTTP 200 response, whose reported SHA-256 was `8a83e7395115d3e7d9e99c3bed934d62a2c80bd981a17200824e4555d659ffd7` but whose raw bytes have not been provided here for comparison.

| Source / handoff accounting | Rows or items |
| --- | ---: |
| Total source rows | 147 |
| Structurally clean but House-source unverified, retained on HOLD | 75 |
| Quarantined E1.18 human review tasks | 72 |
| Source-linked candidate PDF document requests | 17 |
| Quarantined rows linked to these document requests | 51 |
| Tasks needing independent original filing identification | 21 |
| Suspected semantic duplicate groups, still unresolved | 12 |
| Blank primary/secondary reviewer slots | 144 |
| Authenticated original House PDFs | **0** |
| Accepted human review events | **0** |
| Rows approved for ingestion or trading | **0** |

The first two candidate document requests by review-task coverage (not independent authenticity or a trading recommendation) are `2023_20022260.pdf` (**11 tasks**) and `2026_20033725.pdf` (**10 tasks**). The associated URLs in the CSV were supplied by Tracefour. **No link was opened or verified in this milestone.** The 21 missing-source-link tasks need independent filing discovery before their transaction lines can be reviewed. The 75 structurally clean rows still require independent source verification; they were not automatically promoted to the human task queue.

## Reproduce the offline handoff

Run from the checked-out isolated branch, using only your **local** archived E1.23 folder:

```bash
python congress_review_handoff.py /path/to/e123_outputs --output-dir /path/to/new_e125_review_packet
```

Artifacts in the newly created directory:

- `pelosi_e125_document_requests.csv`: one row per candidate PDF with request IDs, original linked task IDs and explicit unauthenticated status;
- `pelosi_e125_human_task_board.csv`: 72 task references, original classifications, immutable source row hashes, observed action/asset and pending status;
- `pelosi_e125_missing_provenance.csv`: 21 separate unresolved tasks **without** invented URLs;
- `pelosi_e125_duplicate_groups.csv`: 12 unmerged groups, with all member IDs and outstanding PDF references;
- `pelosi_e125_two_reviewer_template.csv`: exact, unmodified E1.19 blank template suitable as an **input to E1.19 after two real people have reviewed source documents**;
- `pelosi_e125_manifest.json`: all output SHA-256 checksums, source manifest hash and explicit false ingestion/trading flags.

## How to proceed once real evidence is provided

1. **Request authentic source PDFs from the user**; do not autonomously retrieve them. E1.20 only parses and hashes explicitly supplied local files. A PDF matching a candidate filename or hash alone does **not** establish government authenticity.
2. Two **distinct actual humans** independently inspect each original transaction line and populate primary/secondary entries in the supplied E1.19-compatible CSV. Preserve original page number, exact excerpt, file SHA-256, reviewer identity and timestamps. This does not authenticate reviewer identities cryptographically.
3. Run the existing offline E1.19 `congress_adjudication.py` with `--reviews-csv` and `--pdf-dir` on those explicitly supplied PDFs. Matching reviews remain `two_reviewers_concordant_offline_only`; disagreements and all duplicate groups need separate resolution. E1.19 never permits Supabase writes or trading.
4. A future source-verification milestone must independently authenticate each filing and establish actual point-in-time observation provenance before any ingestion proposal. A **separate explicit approval** is required for database sync, external fetching, scheduled automation, merging, deployment or order execution.

## Verification

Tests use synthetic source files and the already uploaded archival dataset, with `e116_network_guard` installed. No HTTP/DNS requests, original PDF fetches, Supabase access, broker calls, cron, merge or intentional deployment occur in E1.25 code. Tests cover source hash chains, task/doc conservation, blank two-reviewer template consistency, attempted reviewer and source forgery, local file/CSV risks, deterministic output, and the archived 17/21/12 breakdown. CI-style tests skip source-dependent cases when the private snapshot is not present. GitHub Actions outcome must be checked separately; a Vercel deployment status is **not** pytest evidence.

**Release decision: `NO_GO_UNVERIFIED_REVIEW_EVIDENCE`**. All 147 rows remain on HOLD and none is a verified trade signal.
