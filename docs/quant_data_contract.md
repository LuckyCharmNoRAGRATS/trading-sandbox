# Quant AI — Market Data Contract v1.0 (feature/quant-data)

**Ownership:** `quant_data/`, `tests/test_quant_data.py`, this document only. Existing `quant_engine.DataLayer`, `data_fetcher.py`, `ai/`, OMS, strategies and caches are unchanged.

## Existing ingestion (inspected against `main` at `4bb3eee`)

- `quant_engine.DataLayer.get_bars(symbol, days, timeframe, force_refresh)` reads/writes `data/{SYMBOL}_{TIMEFRAME}_master.parquet`, uses Alpaca `DataFeed.IEX` with `Adjustment.ALL`, returns a bare pandas DataFrame, incrementally fetches from `last_cached_time + 1 minute`, deduplicates with `keep='last'`, and forward-fills. Its freshness threshold is 1 day for daily bars or 0.05 days for other frames; its cache does not record feed, adjustment, request interval or provenance. The `days` parameter is a calendar-day lookback, not session count.
- `data_fetcher.get_historical_bars(symbol, days, timeframe, force_refresh)` writes `data/{SYMBOL}_{TIMEFRAME}_{days}d.parquet`; it uses Alpaca IEX and a 15-minute end offset but does not explicitly select corporate-action adjustment and reuses its cache without a freshness check.
- `ai/train_model.py`, `ParameterGridSearch.py`, `live_paper_trader.py` consume `DataLayer`; `cogs/ai_agents.py` and `stats_analyzer.py` consume `data_fetcher`. `tests/test_data_layer.py` exercises old cached-data behaviour. `tests/test_risk_manager.py` contains an outdated three-argument filename assertion; `tests/pytest.ini` points to `test`, not `tests`. No established unified dataset contract exists in these paths.

## Owned public API

| API | Interface | Fail-closed guarantee |
|---|---|---|
| `BarSpec` | Symbol, timeframe (`1Day`, `1Hour`, `1Min`), source, feed, adjustment (`raw/split/dividend/all/unknown`), exchange | Explicit, immutable metadata; source claim **not** proof that adjustments are applied |
| `Session`, `SessionCalendar` | Explicit timezone-aware NYSE session open/close schedule supplied by caller | Does not confuse weekends/holidays/half-days with missing bars; no fabricated exchange calendar |
| `normalize_bars(raw, spec)` | Pandas DataFrame with UTC `DatetimeIndex` named `timestamp`, float64 OHLCV | Handles Alpaca `(symbol,timestamp)` MultiIndex; rejects timezone-naive and missing columns; never fills or deduplicates |
| `audit_bars(...)` | `QualityReport(rows, duplicates, invalid_ohlcv, missing_bars, off_session, outside_window, calendar_verified)` | Identifies OHLC inconsistency, bad volume, duplicates and schedule gaps |
| `build_dataset(..., strict=True)` | `Dataset(bars, manifest, quality)` | Refuses quality failures; diagnostic mode (`strict=False`) never implies verified data |
| `write_dataset(dataset, root)` | `<dataset_id>.parquet` plus `<dataset_id>.manifest.json` | Only accepts passing quality reports; immutable write, no overwrite |
| `read_dataset(root, dataset_id, max_age=None, now=None)` | Verified `Dataset` | Verifies schema/content hash/row count; optional cached-data TTL checks provenance retrieval time |
| `Dataset.to_legacy_frame()` | Deep-copy OHLCV DataFrame | Adapter for backtesting and ML without modifying their execution or strategy code |

**Snapshot version:** `schema_version="1.0"`, content SHA-256 of canonical normalized OHLCV, dataset ID from SHA-256 of schema + data digest + specification + requested UTC window. The manifest also records request window, retrieval UTC time, declared source/feed/adjustments and quality checks. These are **data versions**, not a guarantee of surviving corporate-action corrections or point-in-time vendor availability.

**Calendar rule:** Caller must provide actual exchange sessions, including holidays and partial closes. For daily Alpaca bars, compare exchange-local **dates**, not the midnight-stamped bar time against 09:30 session opens. For intraday bars, compare to session-boundary grids. IEX may legitimately omit intraday intervals with no trades; the report flags gaps and strict ingestion rejects them until a source-specific policy is approved. Calendar coverage itself is a **caller-provided assumption**, not independently verified.

**Staleness rule:** `read_dataset(..., max_age=..., now=...)` compares the manifest's `retrieved_at_utc` with `now` (injected for reproducible tests). It does **not** infer that a Friday final bar is stale on Saturday, or claim broker availability. Consumers must select their own maximum age and exchange session coverage.

## Integration proposal for Master Architecture review

1. Backtesting and ML can consume `Dataset.to_legacy_frame()` after `read_dataset(..., max_age=...)` succeeds. Preserve existing column/index semantics. Record `dataset.manifest["dataset_id"]` in run artifacts so results are reproducible.
2. Build an **external** Alpaca IEX adapter in a later approved change. It must bind `StockBarsRequest(feed=IEX, adjustment=...)` to `BarSpec` and record request/retrieval metadata; never infer adjustments from numeric prices. No fetches happen in `quant_data` v1.
3. Do not replace `DataLayer`, update training paths, delete old cache, modify strategy signals, or route this module into execution until the Master Architecture owner approves dataset interface and migration plan.
4. Coordinate the `Dataset` schema version, exception policy and required session schedule with Backtesting, ML, Research, Risk and the Master Architecture chat before shared-file integration.

## Acceptance criteria & limits

- Offline deterministic synthetic tests for UTC/DST, NYSE weekends/partial sessions, missing bars, duplicates, invalid OHLCV, Alpaca MultiIndex, snapshot integrity, stale-cache handling and immutable writes.
- No network, credentials, broker/order calls, database changes, deployments, external notifications or runtime trading modifications.
- Requires existing `pandas`, `numpy`, `pyarrow` (already pinned in repository). Calendar data must be supplied; no new dependency or licensed exchange reference data bundled.
- `read_dataset` verifies byte-level data semantics after Parquet decoding; content SHA-256 is a reproducibility checksum, not a tamper-proof digital signature.
- The v1 package is a safe research building block, **not** a drop-in live feed or execution readiness signal.
