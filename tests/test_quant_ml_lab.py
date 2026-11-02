"""Deterministic, no-network test suite for the isolated ML lab."""
import json
import numpy as np
import pandas as pd
import pytest
from quant_ml import (
    FEATURE_COLUMNS, PurgedEmbargoSplit, ResearchInferenceRequest,
    build_drift_baseline, make_lagged_features, predict_research_only,
    report_population_stability, run_research_experiment,
    save_local_artifact, triple_barrier_events,
)


def bars(n=720):
    rng = np.random.default_rng(20261102)
    log_returns = rng.normal(0.0002, 0.014, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"close": 100.0 * np.exp(log_returns.cumsum())}, index=idx)


def dataset():
    frame = bars()
    # Deterministic alternating signals; independent of current/future bar.
    signals = pd.Series(np.where(np.arange(len(frame)) % 3 == 0, 1, -1), index=frame.index)
    return make_lagged_features(frame), triple_barrier_events(
        frame, signals, profit_target=.02, stop_loss=.02, max_hold_bars=5
    )


def test_features_strict_one_bar_causality():
    frame = bars(100)
    base = make_lagged_features(frame)
    altered = frame.copy()
    altered.iloc[80, 0] *= 100
    new = make_lagged_features(altered)
    pd.testing.assert_series_equal(base.iloc[80], new.iloc[80])
    assert list(base) == list(FEATURE_COLUMNS)
    assert not base.iloc[80].isna().any()


def test_labels_omit_incomplete_horizon_and_capture_end():
    index = pd.date_range("2026-01-01", periods=5, tz="UTC")
    frame = pd.DataFrame({"close": [100, 103, 100, 97, 96]}, index=index)
    sig = pd.Series([1, -1, 1, 1, 1], index=index)
    result = triple_barrier_events(frame, sig, max_hold_bars=2)
    assert list(result.index) == list(index[:3])
    assert result.iloc[0].target == 1
    assert result.iloc[0].event_end == index[1]
    assert result.iloc[1].target == 1  # short from 103 to 100


def test_purging_and_embargo_both_sides():
    idx = pd.date_range("2026-01-01", periods=60, tz="UTC")
    e = pd.DataFrame({"event_end": idx + pd.Timedelta("4D"),
                      "direction": 1, "target": np.arange(60) % 2}, index=idx)
    for mode in (True, False):
        splitter = PurgedEmbargoSplit(n_splits=3, embargo=pd.Timedelta("3D"), walk_forward=mode)
        for tr, va in splitter.split(e):
            validation_start = e.index[va[0]]
            validation_end = e.iloc[va].event_end.max()
            selected = e.iloc[tr]
            assert not ((selected.index <= validation_end) & (selected.event_end >= validation_start)).any()
            assert not ((selected.index > validation_end) &
                        (selected.index <= validation_end + pd.Timedelta("3D"))).any()
            if mode:
                assert tr.max() < va.min()


def test_experiment_repeatable_and_inference_is_not_order_gate():
    features, events = dataset()
    model_a, scores_a = run_research_experiment(features, events, n_splits=3, seed=7)
    model_b, scores_b = run_research_experiment(features, events, n_splits=3, seed=7)
    assert model_a.model_version == model_b.model_version
    assert scores_a == scores_b
    assert len(scores_a) == 3
    assert all(0 <= row["brier"] <= 1 for row in scores_a)
    ts = features.dropna().index[-10]
    request = ResearchInferenceRequest("SPY", ts, model_a.model_version,
                                       features.loc[ts].to_dict())
    prediction = predict_research_only(model_a, request)
    assert 0 <= prediction.probability_profit_target <= 1
    assert prediction.use == "RESEARCH_ONLY"
    assert not hasattr(prediction, "approved")
    with pytest.raises(ValueError):
        predict_research_only(model_a, ResearchInferenceRequest(
            "SPY", ts, "wrong-version", request.features))
    with pytest.raises(ValueError):
        predict_research_only(model_a, ResearchInferenceRequest(
            "SPY", ts, model_a.model_version, {"returns_1d": 0.01}))


def test_drift_and_versioned_manifest(tmp_path):
    features, events = dataset()
    clean = features.dropna()
    baseline = build_drift_baseline(clean.iloc[:200])
    same = report_population_stability(baseline, clean.iloc[:200])
    shifted = report_population_stability(baseline, clean.iloc[:200] + 10)
    assert shifted["returns_1d"] > same["returns_1d"]
    model, scores = run_research_experiment(features, events, n_splits=3)
    artifact, manifest = save_local_artifact(model, tmp_path, scores)
    data = json.loads(manifest.read_text())
    assert data["model_version"] == model.model_version
    assert data["execution_status"] == "RESEARCH_ONLY"
    assert artifact.exists()
    with pytest.raises(FileExistsError):
        save_local_artifact(model, tmp_path, scores)


def test_invalid_indexes_rejected():
    frame = bars(70)
    naive = frame.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(ValueError):
        make_lagged_features(naive)


def test_preprocessor_fitted_only_on_purged_pre_calibration_history():
    from quant_ml.research import _dataset, _fit_one
    features, events = dataset()
    x, e = _dataset(features, events)
    train_x, train_e = x.iloc[:210], e.iloc[:210]
    classifier, _ = _fit_one(train_x, train_e, seed=42, calibration_fraction=.2)
    cut = int(len(train_x) * .8)
    cal_start = train_e.index[cut]
    eligible = train_e.iloc[:cut].event_end < cal_start
    expected = train_x.iloc[:cut].loc[eligible].mean().to_numpy()
    np.testing.assert_allclose(classifier.named_steps["scaler"].mean_, expected)
    # Calibration-only perturbations must not change preprocessor parameters.
    changed = train_x.copy()
    changed.iloc[cut:] += 1_000_000
    refit, _ = _fit_one(changed, train_e, seed=42, calibration_fraction=.2)
    np.testing.assert_allclose(refit.named_steps["scaler"].mean_, expected)


def test_folds_fail_closed_without_two_classes():
    features, events = dataset()
    events["target"] = 0
    with pytest.raises(ValueError, match="both classes"):
        run_research_experiment(features, events)
