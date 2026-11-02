# E1.20 — Offline House PDF Evidence Request & Intake Preparation

**Decision: NO-GO.** This milestone is a local document-request manifest and a parser-level preflight for *future user-provided files*, **not** a House-PDF retrieval, an authentication service, an adjudication, an ingest workflow, or a trading strategy. This stage makes **zero** external requests, Supabase changes, orders, merges or intentional deployments.

## Source and scope

- Repo: `LuckyCharmNoRAGRATS/trading-sandbox`.
- Branch: **`feature/e116-congress-readiness` only**.
- Source: the original user-uploaded Tracefour member JSON, carried through E1.17 and E1.18 local SHA-256 manifests.
- Source JSON SHA-256: `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`.
- E1.18 manifest SHA-256: `0d2a5bf8f55f052dd53241f3f96516886e71cb9ee92ec1defaa69889a42ef2e2`.
- E1.19: 72 pending two-reviewer adjudications; no independent original filing evidence and zero completed reviews.

## Actual local results

| Measurement | Value |
| --- | ---: |
| Total original provider records | 147 |
| Unresolved E1.18 quarantine tasks | **72** |
| E1.18 tasks with a structurally valid official-host PDF URL supplied by the provider | **51** |
| Distinct source-referenced PDFs (deduplicated document requests, not transaction deduplication) | **17** |
| Tasks missing a structurally valid source filing URL | **21** |
| Suspected semantic duplicate groups requiring original transaction-line comparison | **12** |
| User-provided, filename-matched local House PDFs for E1.20 | **0** |
| Independently authenticated official House PDFs | **0** |
| Auto-promoted, traded, or imported rows | **0** |

### Highest-leverage request priorities

The local request manifest orders source-referenced PDFs by highest unresolved task priority, then task coverage. These are **unverified provider-submitted URL strings**; no network connection or authenticity check has occurred.

| Suggested local PDF basename | Number of linked review tasks | Priority |
| --- | ---: | --- |
| `2023_20022260.pdf` | 11 | P1 |
| `2026_20033725.pdf` | 10 | P1 |
| `2022_20021837.pdf` | 3 | P1 |
| `2022_20021374.pdf` | 2 | P1 |
| `2024_20025535.pdf` | 2 | P1 |
| `2025_20026590.pdf` | 2 | P1 |

The first **two PDFs potentially cover 21 review tasks**, but they do not address the 21 missing-link tasks automatically and do not resolve any duplicate group without original transaction-line evidence and two independent reviewers.

### Output files (local, never committed to Git)

- `pelosi_e120_house_pdf_request_manifest.csv`: 17 distinct PDF requests, expected local basenames, linked task IDs, priority and parser status; every row has `pdf_origin_authenticated=false` and `eligible_for_execution=false`.
- `pelosi_e120_missing_provenance_queue.csv`: 21 separate manual identification tasks. A suggested link copied from a related semantic group is **not** silently promoted to official provenance.
- `pelosi_e120_duplicate_group_evidence.csv`: all 12 suspicious groups, every linked source URL and still-missing member link, all unresolved.
- `pelosi_e120_manifest.json`: frozen source SHA-256, counts, and SHA-256 checksums for each output file.

## Offline use

From the isolated GitHub branch, with the **E1.18 generated files** in a directory (E1.18 user-derived inputs are excluded from Git):

```bash
python congress_evidence_intake.py /path/to/e118_outputs --output-dir /path/to/e120_outputs
```

Only **after the user supplies PDF files**, put them in a dedicated local directory under the suggested basenames (e.g. `2026_20033725.pdf`), and run:

```bash
python congress_evidence_intake.py /path/to/e118_outputs \
  --local-pdf-dir /path/to/user_supplied_house_pdfs \
  --output-dir /path/to/e120_outputs
```

The source URLs are **never fetched**. The module accepts only the exact official-host URL form as a suggested PDF request; it rejects spoofing, fragments and query strings. It reads **only** the exact expected local basename; unrelated PDFs are never treated as task evidence. For a matching user-supplied file, it requires a regular non-symlink file under 20 MB, PDF header, an unencrypted parseable document and a reasonable page count. It records SHA-256 and page count **without** making any claim that this file came from Congress or that any particular transaction is in it. A reviewer must still examine the actual PDF's cited transaction line and complete E1.19 two-independent-reviewer validation.

**Important:** a file being parseable, a filename matching the source link, or a SHA-256 digest being stable does **not** establish authenticity, original publication date, source completeness, or historical knowability. Even matching two-reviewer decisions cannot bypass the E1 execution and database gates. Nothing here infers `first_seen_at`.

## Verification

### Commands executed locally

```bash
cd /mnt/data/e120_work
PYTHONPATH=tests:. E120_E118_DIR=/mnt/data E119_E118_DIR=/mnt/data \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q -p no:cacheprovider \
  --junitxml=/mnt/data/e120_outputs/e120_local_junit.xml \
  tests/test_e119_adjudication.py tests/test_e120_evidence_intake.py

python congress_evidence_intake.py /mnt/data --output-dir /mnt/data/e120_outputs/files
```

**45 passed, 0 failed** (E1.19 + E1.20), including optional private-data tests on the exact uploaded E1.18 outputs. Synthetic PDF parser evidence is explicitly marked origin-unverified. The entire older E1.15–E1.18 suite was **not** re-run locally during this milestone. GitHub Actions Python CI status is still independently unverified; a Vercel `success` status is not proof of pytest completion.

## Human next action and approval boundary

No external PDF retrieval was authorized. The actionable next step is for the user to **supply original House filings**, prioritizing `2023_20022260.pdf` and `2026_20033725.pdf`, or explicitly authorize a separate, narrowly scoped read-only document retrieval. To resolve the 21 missing-link cases, a person must first independently locate and check the matching filing and transaction line. Do not copy links automatically from potential duplicate records. No Supabase writes, sync activation, trading, merge or deployment is authorized.
