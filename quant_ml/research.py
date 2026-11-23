"""Reproducible, calibrated, offline-only binary meta-label research."""
from dataclasses import dataclass
from typing import Any
import hashlib
import json
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss, log_loss
from .contracts import FEATURE_COLUMNS, SCHEMA_VERSION, validate_events, validate_features
from .cv import PurgedEmbargoSplit


@dataclass(frozen=True)
class ResearchModel:
    model_version: str
    estimator: Any
    calibrator: Any
    training_digest: str

    def predict_probability(self, frame: pd.DataFrame) -> np.ndarray:
        validate_features(frame)
        raw = self.estimator.predict_proba(frame.loc[:, list(FEATURE_COLUMNS)])[:, 1]
        return self.calibrator.predict_proba(raw.reshape(-1, 1))[:, 1]


def _dataset(features: pd.DataFrame, events: pd.DataFrame):
    validate_events(events)
    validate_features(features, allow_missing=True)
    if not events.index.isin(features.index).all():
        raise ValueError("missing event features")
    x = features.loc[events.index].copy()
    # Reject the feature warm-up interval, do not replace unobserved history.
    usable = x.notna().all(axis=1)
    return x.loc[usable], events.loc[usable]


def _fit_one(x: pd.DataFrame, events: pd.DataFrame, *, seed: int, calibration_fraction: float):
    if len(x) < 30 or not 0.1 <= calibration_fraction <= 0.4:
        raise ValueError("insufficient data or invalid calibration_fraction")
    cut = int(len(x) * (1 - calibration_fraction))
    calibration_start = events.index[cut]
    # Purge events with labels that could see the calibration period.
    fit_mask = events.iloc[:cut]["event_end"] < calibration_start
    fit_x = x.iloc[:cut].loc[fit_mask]
    fit_y = events.iloc[:cut].loc[fit_mask, "target"].astype(int)
    cal_x, cal_y = x.iloc[cut:], events.iloc[cut:]["target"].astype(int)
    if len(fit_y) < 10 or fit_y.nunique() != 2 or cal_y.nunique() != 2:
        raise ValueError("fit and calibration windows require both classes")
    estimator = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("classifier", LogisticRegression(max_iter=1000, random_state=seed)),
    ])
    estimator.fit(fit_x, fit_y)
    raw_cal = estimator.predict_proba(cal_x)[:, 1]
    calibrator = LogisticRegression(max_iter=1000, random_state=seed)
    calibrator.fit(raw_cal.reshape(-1, 1), cal_y)
    return estimator, calibrator


def run_research_experiment(features: pd.DataFrame, events: pd.DataFrame, *,
                            n_splits: int = 3, embargo: pd.Timedelta = pd.Timedelta("2D"),
                            calibration_fraction: float = 0.2, seed: int = 42):
    """Return (frozen research model, CV scores). Never connects to an execution system."""
    x, y_events = _dataset(features, events)
    splits = PurgedEmbargoSplit(n_splits=n_splits, embargo=embargo, walk_forward=True)
    scores = []
    for fold, (train, test) in enumerate(splits.split(y_events), start=1):
        train_x, train_e = x.iloc[train], y_events.iloc[train]
        estimator, calibrator = _fit_one(train_x, train_e, seed=seed, calibration_fraction=calibration_fraction)
        raw_test = estimator.predict_proba(x.iloc[test])[:, 1]
        probs = calibrator.predict_proba(raw_test.reshape(-1, 1))[:, 1]
        true = y_events.iloc[test]["target"].astype(int).to_numpy()
        scores.append({"fold": fold, "n_train": len(train), "n_test": len(test),
                       "brier": float(brier_score_loss(true, probs)),
                       "log_loss": float(log_loss(true, probs, labels=[0, 1]))})
    estimator, calibrator = _fit_one(x, y_events, seed=seed, calibration_fraction=calibration_fraction)
    digest_input = pd.concat([x, y_events[["target", "event_end"]]], axis=1).to_csv(index=True).encode("utf-8")
    digest = hashlib.sha256(digest_input).hexdigest()
    config = {"schema": SCHEMA_VERSION, "digest": digest, "seed": seed,
              "calibration_fraction": calibration_fraction, "cv_splits": n_splits,
              "embargo": str(embargo)}
    version = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    return ResearchModel(model_version=version, estimator=estimator,
                         calibrator=calibrator, training_digest=digest), scores
