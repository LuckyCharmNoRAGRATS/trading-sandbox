import json
import numpy as np
import pandas as pd
import pytest
from quant_research import ExperimentSpec, make_signals, run_experiment, validate_bars, write_report
from strategies.mean_reversion import BollingerMeanReversion


def bars(n=240):
    rng = np.random.default_rng(23)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n)))
    return pd.DataFrame({"close": close}, index=pd.date_range("2020-01-01", periods=n, tz="UTC"))


def spec(**overrides):
    values = dict(experiment_id="synthetic-sma", strategy="sma",
                  parameter_grid={"fast_window": (3, 5), "slow_window": (12, 18)},
                  train_bars=90, test_bars=30, step_bars=30, cost_bps=8., slippage_bps=2.)
    values.update(overrides)
    return ExperimentSpec(**values)


def test_determinism_oos_nonoverlap_and_json(tmp_path):
    result = run_experiment(bars(), spec())
    assert result == run_experiment(bars(), spec())
    assert (result["fold_count"], result["oos_bars"], result["candidate_count"]) == (5, 150, 4)
    assert sum(result["selection_frequency"].values()) == 5
    for a, b in zip(result["folds"], result["folds"][1:]):
        assert a["test_end"] < b["test_start"]
    path = tmp_path / "report.json"
    write_report(result, path)
    assert json.loads(path.read_text()) == result
    assert "NaN" not in path.read_text() and "Infinity" not in path.read_text()


def test_synthetic_future_mutation_leaves_past_signals_unchanged():
    original, altered = bars(120), bars(120)
    altered.iloc[85:, 0] *= 50
    for name, params in (("sma", {"fast_window": 3, "slow_window": 12}),
                         ("bollinger", {"window": 12, "num_std": 1.5})):
        pd.testing.assert_series_equal(make_signals(original, name, params).iloc[:85],
                                       make_signals(altered, name, params).iloc[:85])


def test_train_selection_not_affected_by_oos_data():
    original, altered = bars(120), bars(120)
    altered.iloc[90:, 0] *= 10
    one = run_experiment(original, spec())["folds"][0]
    two = run_experiment(altered, spec())["folds"][0]
    assert one["selected_params"] == two["selected_params"]
    assert one["train_sharpe"] == two["train_sharpe"]


def test_one_bar_lag_and_turnover_friction():
    from quant_research.core import _slice_returns
    data = pd.DataFrame({"close": [100., 200., 200., 200.]},
                        index=pd.date_range("2020-01-01", periods=4, tz="UTC"))
    signal = pd.Series([0., 1., 1., 1.], index=data.index)
    net, benchmark, turn = _slice_returns(data, signal, 0, 4, 10., 0.)
    assert net[1] == 0.
    assert net[2] == pytest.approx(-0.001)
    assert benchmark[1] == 1.
    assert turn == 1.


def test_cost_sensitivity_monotonic():
    result = run_experiment(bars(), spec())["cost_sensitivity"]
    assert result["0.0"]["total_return"] >= result["1.0"]["total_return"]
    assert result["1.0"]["total_return"] >= result["2.0"]["total_return"]


def test_bollinger_parity_and_research():
    data = bars(100)
    old = BollingerMeanReversion(window=12, num_std=1.5).generate_signals(data)["signal"]
    new = make_signals(data, "bollinger", {"window": 12, "num_std": 1.5})
    pd.testing.assert_series_equal(old, new, check_names=False)
    design = ExperimentSpec("bb", "bollinger", {"window": (12,), "num_std": (1.5, 2.)}, 80, 10, 10)
    assert run_experiment(data, design)["candidate_count"] == 2


def test_rejects_gaps_duplicates_and_overlapping_windows():
    data = bars(15)
    data.iloc[3, 0] = np.nan
    with pytest.raises(ValueError, match="finite positive"):
        validate_bars(data)
    with pytest.raises(ValueError, match="non-overlapping"):
        spec(step_bars=10)
    with pytest.raises(ValueError, match="Insufficient"):
        run_experiment(bars(50), spec())
    repeated = bars(20)
    repeated.index = repeated.index.where(np.arange(20) != 2, repeated.index[1])
    with pytest.raises(ValueError, match="unique"):
        validate_bars(repeated)
