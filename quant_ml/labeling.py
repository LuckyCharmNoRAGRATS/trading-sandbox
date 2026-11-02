"""Close-to-close triple barriers; never label incomplete future windows."""
import numpy as np
import pandas as pd
from .contracts import utc_index, validate_events


def triple_barrier_events(bars: pd.DataFrame, signals: pd.Series, *,
                          profit_target: float = 0.02, stop_loss: float = 0.01,
                          max_hold_bars: int = 10) -> pd.DataFrame:
    utc_index(bars, "bars")
    if not 0 < profit_target < 1 or not 0 < stop_loss < 1 or max_hold_bars < 1:
        raise ValueError("invalid barrier configuration")
    if "close" not in bars or not signals.index.equals(bars.index):
        raise ValueError("signal index must equal bar index and close column is required")
    prices = bars["close"].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any() or not signals.isin([-1, 0, 1]).all():
        raise ValueError("invalid prices or directions")
    records, timestamps = [], []
    directions = signals.to_numpy(dtype=int)
    for i, direction in enumerate(directions):
        if direction == 0 or i + max_hold_bars >= len(bars):
            continue  # Incomplete future horizon is not an observed 0 label.
        entry = prices[i]
        end_pos, outcome = i + max_hold_bars, 0
        for j in range(i + 1, end_pos + 1):
            signed_return = direction * (prices[j] / entry - 1.0)
            if signed_return >= profit_target:
                end_pos, outcome = j, 1
                break
            if signed_return <= -stop_loss:
                end_pos, outcome = j, -1
                break
        timestamps.append(bars.index[i])
        records.append((bars.index[end_pos], direction, outcome, int(outcome == 1)))
    result = pd.DataFrame(records, index=pd.DatetimeIndex(timestamps, tz="UTC"),
                          columns=["event_end", "direction", "barrier_outcome", "target"])
    result["event_end"] = pd.to_datetime(result["event_end"], utc=True)
    validate_events(result)
    return result
