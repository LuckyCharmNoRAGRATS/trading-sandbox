# Quant Research Lab — offline v1

This package is **research-only**. Imports depend on `numpy` and `pandas` and never create broker clients, retrieve market data, load a model, send orders, or enable live trading. Existing `quant_engine.py`, strategies, and risk modules remain unchanged.

## Interfaces

- `ExperimentSpec(experiment_id, strategy, parameter_grid, train_bars, test_bars, step_bars, embargo_bars=0, cost_bps=8.0, slippage_bps=0.0, annualization=252, stress_multipliers=(0,1,2))`. Strategies: `sma` (fast_window, slow_window), `bollinger` (window, num_std). At most 100 unique preregistered candidates. Costs and slippage are **bps per one unit of absolute position turnover** (a -1→+1 flip is 2 units).
- `validate_bars(bars)` requires increasing, unique, timezone-aware timestamps and positive, nonmissing close prices. Corporate actions, universe membership, timestamps, exchange calendar and feed accuracy must be audited upstream.
- `make_signals(bars, strategy, params)` mirrors the original SMA and Bollinger rules without importing `quant_engine`. It outputs an indexed Series with positions in -1,0,+1. Parity tests guard the Bollinger mirror.
- `run_experiment(bars, spec)` uses fixed-length rolling training windows and disjoint OOS windows (optional embargo). Selects parameters **only** on train Sharpe; computes OOS at t using previous bar's signal and current close-to-close return. An OOS fold starts flat and pays entry friction, but does not forcibly liquidate at its final bar. Passive long benchmark uses identical OOS dates without fees. Aggregated fold performance, parameter frequency, benchmark, turnover and cost/slippage stress scenarios are in the returned JSON-friendly dict.
- `write_report(report, path)` writes deterministic JSON to a caller-selected local path. SHA-256 fingerprints cover the normalized experiment specification and close-price/timestamp dataset.

## Example

```python
import pandas as pd
from quant_research import ExperimentSpec, run_experiment, write_report

bars = pd.read_parquet("data/curated_daily_bars.parquet") # never downloads
spec = ExperimentSpec(
    "sma-v1", "sma", {"fast_window": (10, 20), "slow_window": (40, 60)},
    train_bars=252, test_bars=63, step_bars=63,
    embargo_bars=1, cost_bps=8.0, slippage_bps=2.0,
)
write_report(run_experiment(bars, spec), "research_reports/sma-v1.json")
```

Run `python -m pytest -q tests/test_quant_research.py` with dependencies installed.

## Audit and integration contract for Master Architecture

- `quant_engine.SMACrossover.generate_signals(df)` returns a NumPy array, while `strategies.mean_reversion.BollingerMeanReversion.generate_signals(df)` returns a DataFrame. `FrictionBacktester.run(df, strategy)` currently assumes a one-column signal/Series and returns rounded aggregate metrics rather than an OOS return series. Do not feed a Bollinger object to this backtester directly.
- `quant_engine.RegimeAnalyzer.evaluate(close_prices)` performs ADF and Hurst calculations; `rolling_hurst` is causal, but `evaluate` summarizes its entire input history. Only call it on historical training slices if integrated later. `stats_analyzer.py` contains import-time fetching and is NOT a safe offline dependency.
- `ParameterGridSearch.py` implements a single 70/30 split, not full walk-forward testing, and OOS slices discard preceding indicator context. This package is an alternative **research interface only**, not a strategy/engine replacement.
- No imports from the execution engine, brokers, `ai`, model store, live runner or Discord. Integration should consume versioned JSON after review, not call live code from research.

## Scientific safeguards and limits

- Synthetic tests cover prefix causality for SMA/Bollinger, train-only selection, one-bar lag, turnover charges, non-overlapping folds, cost monotonicity, deterministic JSON and dirty data rejection.
- Record all candidate trials and parameter-selection frequency. **No formal multiple-testing p-value correction** is claimed; selection frequency is a stability diagnostic, not statistical proof. Label-based purging is separate from the simple bar embargo here.
- These are close-to-close simplified returns, not real fills. No borrow fees, impact/capacity, funding, intrabar stops, survivorship-adjusted universe, corporate-action verification, market calendars or production execution. A historical backtest is not evidence of future profitability.
