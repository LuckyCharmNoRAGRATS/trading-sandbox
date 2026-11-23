"""Stable research-only inputs; no execution dependencies."""
from __future__ import annotations

import numpy as np
import pandas as pd

SCHEMA_VERSION = "quant-ml-v1"
FEATURE_COLUMNS = ("returns_1d", "returns_5d", "volatility_20d", "price_to_sma", "hurst")


def utc_index(frame: pd.DataFrame, name: str) -> None:
    idx = frame.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
        raise ValueError(f"{name} requires timezone-aware DatetimeIndex")
    if str(idx.tz) != "UTC":
        raise ValueError(f"{name} requires UTC timestamps")
    if idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError(f"{name} index must be strictly increasing and unique")


def validate_events(events: pd.DataFrame, require_label: bool = True) -> None:
    utc_index(events, "events")
    required = {"event_end", "direction"} | ({"target"} if require_label else set())
    if not required.issubset(events.columns):
        raise ValueError(f"missing event fields: {sorted(required - set(events.columns))}")
    ends = events["event_end"]
    if not pd.api.types.is_datetime64_any_dtype(ends) or getattr(ends.dt, "tz", None) is None:
        raise ValueError("event_end must contain timezone-aware UTC datetimes")
    if str(ends.dt.tz) != "UTC" or ends.isna().any() or (ends.array < events.index.array).any():
        raise ValueError("invalid event end or timezone")
    if not events["direction"].isin([-1, 1]).all():
        raise ValueError("direction must be +1 or -1")
    if require_label and not events["target"].isin([0, 1]).all():
        raise ValueError("target must be binary 0 or 1")


def validate_features(features: pd.DataFrame, allow_missing: bool = False) -> None:
    utc_index(features, "features")
    if tuple(features.columns) != FEATURE_COLUMNS:
        raise ValueError("feature columns or order differ from quant-ml-v1 schema")
    values = features.to_numpy(dtype=float)
    if np.isinf(values).any() or (not allow_missing and not np.isfinite(values).all()):
        raise ValueError("feature values must be finite")
