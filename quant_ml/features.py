"""Single-symbol daily close features observable no later than t-1."""
import numpy as np
import pandas as pd
from .contracts import FEATURE_COLUMNS, utc_index


def _hurst(log_prices: np.ndarray) -> float:
    lags = np.arange(2, 20)
    stds = np.array([np.std(log_prices[k:] - log_prices[:-k]) for k in lags])
    if not np.isfinite(stds).all() or (stds <= 0).any():
        return np.nan
    return float(np.polyfit(np.log(lags), np.log(stds), 1)[0])


def make_lagged_features(bars: pd.DataFrame) -> pd.DataFrame:
    utc_index(bars, "bars")
    if "close" not in bars:
        raise ValueError("close is required")
    close = bars["close"].astype(float)
    if not np.isfinite(close).all() or (close <= 0).any():
        raise ValueError("close must contain positive finite prices")
    change = close.pct_change()
    sma = close.rolling(20).mean()
    unlagged = pd.DataFrame(index=bars.index)
    unlagged["returns_1d"] = change
    unlagged["returns_5d"] = close.pct_change(5)
    unlagged["volatility_20d"] = change.rolling(20).std() * np.sqrt(252)
    unlagged["price_to_sma"] = (close - sma) / sma
    unlagged["hurst"] = np.log(close).rolling(60).apply(_hurst, raw=True)
    return unlagged.loc[:, list(FEATURE_COLUMNS)].shift(1)
