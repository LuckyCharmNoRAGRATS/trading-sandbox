# E2.1 — Synthetic point-in-time event and market-data contract

**Release classification: research-only / synthetic-only / NO-GO for real congressional data, Supabase ingestion and orders.** Scope: `LuckyCharmNoRAGRATS/trading-sandbox`, isolated `feature/e116-congress-readiness` branch. E2.0 is introduced alongside E2.1 because E2.0 was previously a local-only prototype and was not committed to GitHub.

## What is implemented

`congress_e2_data_contract.py` is a deterministic, strictly local **synthetic fixture** interface that makes the E2.0 event-study engine more auditable. It provides:

- `validate_synthetic_dataset(payload)`: validate an explicitly versioned E2.1 synthetic dataset, normalize every timestamp to UTC, refuse real-data provenance, reject malformed/missing/extra fields, verify chronological event disclosure/observation ordering, reject ambiguous assets and actions, semantic duplicates and ambiguous per-bar revision sequences. Returns a frozen `SyntheticDataset` backed by canonical JSON bytes and a SHA-256 digest.
- `load_synthetic_snapshot(local_path, expected_file_sha256=None)`: inspect one locally saved, bounded (2 MB), regular UTF-8 JSON file; reject symlinks, unexpected byte length, mismatched SHA, duplicate JSON keys, NaN/Infinity and oversized input. It **never** fetches a URL.
- `snapshot_asof(dataset, asof=...)`: use only price records whose `bar_time` **and** `available_at` are no later than the as-of research time. For each `(ticker, bar_time)` the selected price is the latest *visible* revision, not a correction published afterward. Event observation times later than as-of are rejected rather than retroactively inferred. The returned view reveals neither future revisions nor trade orders. It revalidates the immutable dataset even if the wrapper was constructed manually.
- `study_synthetic_snapshot(dataset, asof=..., forward_bars=...)`: adapt the validated market snapshot to E2.0's existing **synthetic-only close-to-close educational event study**. E2.0 separately ensures the reference close was available before the forward close. A late market correction that arrived after the reference horizon is **not** used to fabricate a historical reference price.

### Event schema (strict)

One versioned JSON envelope has `schema_version`, `dataset_id`, `dataset_kind`, `price_adjustment_policy`, `events`, and `bar_revisions`. Only `schema_version=E2.1.0-synthetic`, `dataset_kind=synthetic_fixture_only`, and `price_adjustment_policy=synthetic_no_corporate_actions` are accepted.

Each event must contain an invented source ID, unambiguous synthetic event ID, `member=Synthetic Example`, `ticker`, `owner` (`Self` or `Spouse`), `asset_type=Stock`, `action` (`Purchase` or `Sale`), simulated `transaction_date`, simulated `filing_date`, timezone-aware `first_seen_at`, `semantic_duplicate_resolved=true`, and `official_filing_verified=false`. The chronology `transaction_date ≤ filing_date ≤ first_seen_at date` is checked; **the dates do not establish actual real publication times**.

### Price-data schema (strict)

Each market bar revision contains `ticker`, `bar_time`, `available_at`, positive finite `adjusted_close`, and sequential integer `revision` starting at 1, plus `dataset_kind=synthetic_fixture_only`. Revisions for one `(ticker, bar_time)` must have strictly increasing availability timestamps. A base bar cannot be published before its bar time. Duplicate versions, missing version numbers, nonfinite prices and unexpected corporate-action claims are rejected.

The adjustment policy explicitly says **no corporate-action audit was performed**. It would be misleading to treat these invented adjusted closes as split-safe, dividend-safe, licensed, execution-quality prices. The event study is not P&L or a tradable strategy. No claims of real market availability, historical symbol mapping, survivorship neutrality, borrow costs, latency, slippage or market session correctness are made.

### Point-in-time example (entirely invented)

The committed synthetic file `tests/fixtures/e21_synthetic_demo.json` contains three artificial DEMO bar times and a revision to the second bar. The original reference close is **105.0**, available at `2026-11-01T16:01Z`; a revised **106.0** is only available at `2026-11-02T08:00Z`. An E2.1 view at `2026-11-02T07:00Z` must still see **105.0**. An E2.1 view at `2026-11-02T12:00Z` sees **106.0**, and E2.0's educational price change to the invented forward **110.0** at `2026-11-02T09:00Z` is `(110 / 106) - 1`. A price correction first published at `2026-11-02T10:00Z` would **fail** the event study rather than sneak into an earlier reference horizon.

### Reproduce offline

```bash
PYTHONPATH=tests:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p e116_network_guard -p no:cacheprovider -q \
  tests/test_e20_research.py tests/test_e21_data_contract.py
```

Optional local-only E1 hold verification requires a hash-linked E1.23 **private research package** (not committed):

```bash
E20_ARCHIVED_E123_DIR=/path/to/private/e123/replay \
PYTHONPATH=tests:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p e116_network_guard -p no:cacheprovider -q \
  tests/test_e20_research.py tests/test_e21_data_contract.py
```

To run one synthetic example from the repo root:

```python
from datetime import datetime, timezone
from congress_e2_data_contract import load_synthetic_snapshot, study_synthetic_snapshot

sample = load_synthetic_snapshot("tests/fixtures/e21_synthetic_demo.json")
print(study_synthetic_snapshot(sample, asof=datetime(2026, 11, 2, 12, tzinfo=timezone.utc)))
```

Do **not** pass the 147-row Tracefour feed to `load_synthetic_snapshot` or E2.0. Its format is intentionally incompatible.

## E1 source remains blocked

Earlier E1.23–E1.25 audits of the uploaded saved JSON found 147 records on HOLD, with 75 structurally clean yet unverified, 72 human-review tasks, 17 apparent House PDF document requests, 21 missing source links, 12 unresolved potential duplicate groups and 144 blank reviewer slots. Those counts **must not** be converted to signals. Real first-public-observation evidence is absent. The user-reported HTTP 200 proves reachable JSON at one time, not filing authenticity or point-in-time backtest eligibility. Hashes demonstrate internal reproducibility, not external provenance.

The optional E2.0 `assess_real_source_hold()` only rechecks the archived E1.23 hold package, returning zero real eligible events. It has no route into `study_synthetic_snapshot()`.

## What is not implemented

No access to Tracefour, House filings, Alpaca, any historical market-data vendor, Supabase, webhook, scheduling, Discord, trading engine, order router, or Vercel runtime. No database schema mutation; any proposed production data migration is a future offline design-only milestone. E2.2 should add licensed point-in-time vendor snapshots and corporate-action/symbol-master audit **only after explicit approval**, rather than removing this synthetic-only boundary.

**E2.1 quality gate: offline research only. No release or live execution authorization.**
