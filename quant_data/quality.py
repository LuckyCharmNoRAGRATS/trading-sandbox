"""Pure OHLCV normalization and non-destructive data-quality checks."""
import numpy as np
import pandas as pd
from .schema import BarSpec, QualityReport, OHLCV, utc_stamp
from .calendar import SessionCalendar


def normalize_bars(raw: pd.DataFrame, spec: BarSpec) -> pd.DataFrame:
    """Return timestamp-indexed UTC OHLCV; no deduplication or forward-filling."""
    if not isinstance(raw, pd.DataFrame):
        raise TypeError("raw must be a pandas DataFrame")
    frame = raw.copy()
    if isinstance(frame.index, pd.MultiIndex):
        if "symbol" not in frame.index.names:
            raise ValueError("multi-index must have a symbol level")
        frame = frame.xs(spec.symbol, level="symbol", drop_level=True)
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("bar timestamps must be timezone-aware DatetimeIndex")
    if frame.columns.duplicated().any() or any(col not in frame for col in OHLCV):
        raise ValueError("missing or duplicate OHLCV columns")
    frame = frame.loc[:, list(OHLCV)]
    frame.index = frame.index.tz_convert("UTC")
    frame.index.name = "timestamp"
    for col in OHLCV:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    return frame.sort_index(kind="stable")


def audit_bars(frame: pd.DataFrame, spec: BarSpec, *,
               start: pd.Timestamp, end: pd.Timestamp,
               calendar: SessionCalendar | None = None) -> QualityReport:
    """Inspect without altering bars. Missing bars require an explicit schedule."""
    left, right = utc_stamp(start, "start"), utc_stamp(end, "end")
    if left >= right:
        raise ValueError("start must precede end")
    price = frame.loc[:, ["open", "high", "low", "close"]]
    invalid = (
        ~np.isfinite(frame.loc[:, list(OHLCV)].to_numpy()).all(axis=1)
        | (price <= 0).any(axis=1).to_numpy()
        | (frame["volume"] < 0).to_numpy()
        | (frame["high"] < price[["open", "low", "close"]].max(axis=1)).to_numpy()
        | (frame["low"] > price[["open", "high", "close"]].min(axis=1)).to_numpy()
    )
    missing = off = 0
    if calendar is not None:
        if calendar.exchange != spec.exchange:
            raise ValueError("calendar exchange differs from bar specification")
        expected = calendar.expected(spec.timeframe, left, right)
        actual = calendar.identify(frame.index, spec.timeframe)
        missing = len(expected - actual)
        off = len(actual - expected)
    outside = int(((frame.index < left) | (frame.index >= right)).sum())
    return QualityReport(
        rows=len(frame), duplicates=int(frame.index.duplicated().sum()),
        invalid_ohlcv=int(invalid.sum()), missing_bars=missing, off_session=off,
        outside_window=outside, calendar_verified=calendar is not None,
    )
