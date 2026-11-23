"""Deterministic, offline, close-to-close research; never places orders."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExperimentSpec:
    """Precommitted design. Do not alter after examining out-of-sample results."""
    experiment_id: str
    strategy: str
    parameter_grid: dict[str, tuple[int | float, ...]]
    train_bars: int
    test_bars: int
    step_bars: int
    embargo_bars: int = 0
    cost_bps: float = 8.0
    slippage_bps: float = 0.0
    annualization: int = 252
    stress_multipliers: tuple[float, ...] = (0.0, 1.0, 2.0)

    def __post_init__(self):
        if not self.experiment_id.strip():
            raise ValueError("experiment_id required")
        if self.train_bars < 3 or self.test_bars < 2 or self.step_bars < self.test_bars:
            raise ValueError("Require train>=3, test>=2 and non-overlapping test folds")
        if self.embargo_bars < 0 or self.annualization <= 0:
            raise ValueError("Invalid embargo/annualization")
        if not np.isfinite([self.cost_bps, self.slippage_bps]).all() or min(self.cost_bps, self.slippage_bps) < 0:
            raise ValueError("Costs must be finite nonnegative basis points")
        if not self.stress_multipliers or any(not np.isfinite(x) or x < 0 for x in self.stress_multipliers):
            raise ValueError("Invalid stress factors")
        trials = parameter_sets(self.strategy, self.parameter_grid)
        if len(trials) > 100:
            raise ValueError("More than 100 trials: reduce grid to control data snooping")
        if max(max(p.get("slow_window", 0), p.get("window", 0)) for p in trials) >= self.train_bars:
            raise ValueError("Training window must exceed every indicator lookback")


def parameter_sets(strategy, grid):
    names = ("fast_window", "slow_window") if strategy == "sma" else ("window", "num_std") if strategy == "bollinger" else ()
    if not names or set(grid) != set(names) or any(not grid[k] for k in names):
        raise ValueError("Unsupported strategy or invalid parameter grid")
    result = []
    for values in product(*(grid[k] for k in names)):
        p = dict(zip(names, values))
        windows = ("fast_window", "slow_window") if strategy == "sma" else ("window",)
        if any(type(p[k]) is not int or p[k] < 2 for k in windows):
            raise ValueError("Window parameters must be integers >= 2")
        if strategy == "sma" and p["fast_window"] >= p["slow_window"]:
            continue
        if strategy == "bollinger" and (not np.isfinite(p["num_std"]) or p["num_std"] <= 0):
            raise ValueError("num_std must be positive and finite")
        if p not in result:
            result.append(p)
    if not result:
        raise ValueError("No valid parameter candidates")
    return result


def validate_bars(bars):
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("Require timezone-aware DatetimeIndex")
    if not bars.index.is_monotonic_increasing or not bars.index.is_unique:
        raise ValueError("Timestamps must be unique and increasing")
    if "close" not in bars or len(bars) < 3:
        raise ValueError("Need at least three close prices")
    close = pd.to_numeric(bars["close"], errors="raise").astype(float)
    if not np.isfinite(close).all() or (close <= 0).any():
        raise ValueError("Prices must be finite positive and contain no gaps")
    return pd.DataFrame({"close": close}, index=bars.index)


def make_signals(bars, strategy, params):
    """Pure research mirrors of the original SMA and Bollinger algorithms."""
    close = bars["close"]
    if strategy == "sma":
        fast = close.rolling(params["fast_window"]).mean()
        slow = close.rolling(params["slow_window"]).mean()
        return pd.Series(np.where(fast > slow, 1., -1.), index=close.index, name="signal")
    if strategy == "bollinger":
        avg = close.rolling(params["window"]).mean()
        std = close.rolling(params["window"]).std()
        signal = pd.Series(np.nan, index=close.index)
        signal.loc[close < avg - params["num_std"] * std] = 1.
        signal.loc[close > avg + params["num_std"] * std] = -1.
        signal.loc[(close > avg) & (close.shift(1) <= avg.shift(1))] = 0.
        signal.loc[(close < avg) & (close.shift(1) >= avg.shift(1))] = 0.
        return signal.ffill().fillna(0.).rename("signal")
    raise ValueError("Unsupported strategy")


def _metrics(ret, annualization):
    ret = np.asarray(ret, dtype=float)
    if not len(ret) or not np.isfinite(ret).all() or (ret <= -1).any():
        raise ValueError("Invalid returns, including loss >=100%")
    equity = np.cumprod(1 + ret)
    peaks = np.maximum.accumulate(np.r_[1., equity])[1:]
    vol = ret.std(ddof=1) if len(ret) > 1 else 0.
    return {"bars": len(ret), "total_return": float(equity[-1] - 1),
            "annualized_sharpe": float(ret.mean() * np.sqrt(annualization) / vol) if vol > 0 else 0.,
            "max_drawdown": float(np.min(equity / peaks - 1))}


def _slice_returns(bars, signals, start, end, cost_bps, slippage_bps):
    """A position at t uses signal[t-1]; folds start flat and pay entry turnover."""
    if not (0 <= start < end <= len(bars)) or not signals.index.equals(bars.index):
        raise ValueError("Mismatched signal index or slice")
    market = bars["close"].pct_change(fill_method=None).fillna(0.).to_numpy()[start:end]
    pos = signals.shift(1).fillna(0.).to_numpy(dtype=float)[start:end]
    if not np.isfinite(pos).all() or not np.isin(pos, [-1., 0., 1.]).all():
        raise ValueError("Positions must belong to -1, 0, +1")
    turn = np.abs(np.diff(np.r_[0., pos]))
    net = pos * market - turn * (cost_bps + slippage_bps) / 10000.
    return net, market, float(turn.sum())


def run_experiment(bars, spec: ExperimentSpec):
    """Train-only parameter selection with disjoint walk-forward OOS evaluation."""
    data = validate_bars(bars)
    candidates = parameter_sets(spec.strategy, spec.parameter_grid)
    folds, all_oos, all_benchmark, selected = [], [], [], {}
    stress = {str(m): [] for m in spec.stress_multipliers}
    for train_start in range(0, len(data), spec.step_bars):
        train_end = train_start + spec.train_bars
        test_start = train_end + spec.embargo_bars
        test_end = test_start + spec.test_bars
        if test_end > len(data):
            break
        train = data.iloc[train_start:train_end]
        scores = []
        for params in candidates:
            sig = make_signals(train, spec.strategy, params)
            ret, _, _ = _slice_returns(train, sig, 0, len(train), spec.cost_bps, spec.slippage_bps)
            scores.append(_metrics(ret, spec.annualization)["annualized_sharpe"])
        # Grid order is a deterministic tie breaker; OOS returns are never scored here.
        winning_index = max(range(len(scores)), key=lambda i: scores[i])
        params = candidates[winning_index]
        key = json.dumps(params, sort_keys=True, separators=(",", ":"))
        selected[key] = selected.get(key, 0) + 1
        history = data.iloc[:test_end]  # past and present only, never future folds
        signals = make_signals(history, spec.strategy, params)
        net, benchmark, turnover = _slice_returns(history, signals, test_start, test_end,
                                                   spec.cost_bps, spec.slippage_bps)
        all_oos.extend(net)
        all_benchmark.extend(benchmark)
        for multiplier in spec.stress_multipliers:
            rerun, _, _ = _slice_returns(history, signals, test_start, test_end,
                                         spec.cost_bps * multiplier, spec.slippage_bps * multiplier)
            stress[str(multiplier)].extend(rerun)
        folds.append({"train_start": train.index[0].isoformat(),
                      "train_end": train.index[-1].isoformat(),
                      "test_start": history.index[test_start].isoformat(),
                      "test_end": history.index[test_end-1].isoformat(),
                      "selected_params": params, "train_sharpe": scores[winning_index],
                      "oos": _metrics(net, spec.annualization),
                      "benchmark": _metrics(benchmark, spec.annualization),
                      "turnover_units": turnover})
    if not folds:
        raise ValueError("Insufficient data for any full fold")
    spec_json = json.loads(json.dumps(asdict(spec), sort_keys=True, allow_nan=False))
    return {"schema_version": 1, "engine": "quant_research.v1",
            "experiment": spec_json,
            "spec_sha256": sha256(json.dumps(spec_json, sort_keys=True).encode()).hexdigest(),
            "data_sha256": sha256(data.index.asi8.tobytes() + data["close"].to_numpy(dtype="<f8").tobytes()).hexdigest(),
            "input_bars": len(data), "candidate_count": len(candidates),
            "fold_count": len(folds), "oos_bars": len(all_oos),
            "selection_frequency": selected, "folds": folds,
            "aggregate_oos": _metrics(all_oos, spec.annualization),
            "aggregate_buy_hold": _metrics(all_benchmark, spec.annualization),
            "cost_sensitivity": {k: _metrics(v, spec.annualization) for k, v in stress.items()},
            "limitations": [
                "Close-to-close synthetic fills; no broker or intrabar executions.",
                "Selection tests <=100 declared candidates, but no formal multiple-testing correction.",
                "Each OOS fold starts flat; terminal positions are not liquidated.",
                "Benchmark excludes transaction costs; same OOS date windows.",
                "Input provenance, splits, dividends, survivorship and market hours not validated."]}


def write_report(report, path):
    """Explicit local JSON export only; deterministic bytes for the same inputs."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
