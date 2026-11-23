# quant_ml — Research-only interface (v1)

This package is independent of quant_engine, ai/, cogs/, Alpaca, Discord,
order management and strategies. It never makes network requests.
**Do not import it into any order-decision path.**

## Boundary contract

- Input bars: **one symbol**, positive close, unique increasing UTC
  DatetimeIndex; one closed daily bar per row. Treat the bar timestamp
  as the observation timestamp. Data should be split/dividend-adjusted.
- make_lagged_features(bars) returns five float columns in FEATURE_COLUMNS
  order. Feature at row t uses prices no later than t-1. Initial warm-up
  values are NaN and excluded from training.
- Base strategy must independently supply signals in {-1,0,+1}, aligned
  to bars. triple_barrier_events assumes entry at close t, evaluates
  **future closes only**, records event_end at first barrier or expiry,
  sets barrier_outcome in {-1,0,+1}, target=1 for profit target else 0.
  Events without a complete maximum horizon are omitted even if an
  early barrier could be observed. Intrabar barrier order not modeled.
- PurgedEmbargoSplit uses [event start, event_end] overlap. Walk-forward
  CV uses only past training samples. Non-forward diagnostic CV
  additionally embargoes samples after validation. Forward-only CV
  has no post-validation samples on which to apply an embargo.
- run_research_experiment applies preprocessing in each training fold,
  reserves a chronological calibration tail from that fold, and purges
  events overlapping the calibration window. A fold with insufficient
  classes fails closed. Metrics are OOS Brier and log loss, not
  profitability claims. The final model refits on labeled history and
  its final fit is NOT an out-of-sample evaluation.
- predict_research_only requires UTC asof, matching schema/model version,
  all five finite feature values; returns probability_profit_target
  and RESEARCH_ONLY status, never approval, veto, order, or position.
- Drift PSI uses only training-reference bins, at least 30 rows.
  PSI measures feature distribution shift, not concept drift or P&L.
- save_local_artifact writes a versioned joblib and a SHA256 JSON manifest
  to caller-supplied local storage, with no overwrites or model loading.
  Never commit joblib/pkl. Pickle-family files may execute arbitrary code
  when loaded; loading is only for trusted separately verified artifacts.

## Validation

Run from repository root:

    python -m pytest -q tests/test_quant_ml_lab.py

Tests are synthetic and deterministic, with no market feed or model file.
No existing module or strategy behavior is changed in this branch.

## Integration proposal — approval pending

A dedicated research adapter supplies adjusted, timestamped daily bars and
aligned base strategy events. A research-only dashboard may consume immutable
ResearchInferenceResult data. Risk, OMS, broker and live traders must ignore ML
output. Coordinate timestamp semantics, feature schema versioning, model
registry and data adjustments with Master Architecture first.
