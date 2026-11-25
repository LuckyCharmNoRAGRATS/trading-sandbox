"""Replay the paper trader's daily SPY signals using the repository's cached bars."""
from pathlib import Path

import numpy as np
import pandas as pd


def replay(bars, mode="strategy", allocation=0.05, fee=0.0004):
    bars = bars.sort_values("timestamp").reset_index(drop=True)
    prices = bars[["open", "close"]]
    if (len(bars) < 21 or bars.timestamp.isna().any() or bars.timestamp.duplicated().any()
            or not np.isfinite(prices).all().all() or (prices <= 0).any().any()):
        raise ValueError("Expected at least 21 distinct dated bars with positive prices")
    rolling = bars.close.rolling(20)
    z = (bars.close - rolling.mean()) / rolling.std()
    cash, shares = 100_000.0, 0
    curve = [cash]
    entries = exits = 0
    for i in range(20, len(bars)):
        opening, closing = prices.iloc[i]
        prev_close = bars.close.iloc[i - 1]
        score = z.iloc[i - 1]
        order = 0
        if mode == "hold" and i == 20:
            order = int(cash * allocation / (opening * (1 + fee)))
        elif mode == "strategy":
            if shares and abs(score) < 0.5:
                order = -shares
            elif not shares and abs(score) >= 2:
                qty = int(cash * allocation / prev_close)
                order = qty if score < 0 else -qty
        if order:
            entries += int(shares == 0)
            exits += int(shares != 0)
            shares += order
            cash -= order * opening + abs(order) * opening * fee
        curve.append(cash + shares * closing)
    equity = pd.Series(curve)
    return dict(start=bars.timestamp.iloc[20].date(), end=bars.timestamp.iloc[-1].date(),
                net_pct=100 * (equity.iloc[-1] / equity.iloc[0] - 1),
                max_drawdown_pct=100 * (1 - equity / equity.cummax()).max(),
                entries=entries, exits=exits, ending_shares=shares)


if __name__ == "__main__":
    path = Path(__file__).parent / "data" / "SPY_1Day_365d.parquet"
    bars = pd.read_parquet(path).reset_index()
    for label, mode, allocation in [("Mean reversion", "strategy", 0.05),
                                     ("5% buy and hold", "hold", 0.05),
                                     ("Full buy and hold", "hold", 1.0)]:
        print(label, replay(bars, mode, allocation))
