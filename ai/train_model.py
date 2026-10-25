import sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import precision_score, roc_auc_score

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from quant_engine import QuantConfig, DataLayer, RegimeAnalyzer

FEATURE_COLS = ["returns_1d", "returns_5d", "volatility_20d", "price_to_sma", "hurst"]


def build_features_and_triple_barrier(
    df: pd.DataFrame, pt: float = 0.02, sl: float = 0.01, max_hold: int = 10
) -> pd.DataFrame:
    data = df.copy()
    data["returns_1d"] = data["close"].pct_change(1)
    data["returns_5d"] = data["close"].pct_change(5)
    data["volatility_20d"] = data["returns_1d"].rolling(20).std() * np.sqrt(252)
    data["sma_20"] = data["close"].rolling(20).mean()
    data["sma_40"] = data["close"].rolling(40).mean()
    data["price_to_sma"] = data["close"] / data["sma_20"]
    data["hurst"] = (
        data["close"]
        .rolling(30)
        .apply(lambda x: RegimeAnalyzer.hurst_exponent(x, max_lag=15), raw=True)
    )

    # Base Strategy Signal: SMA 20/40
    data["signal"] = np.where(data["sma_20"] > data["sma_40"], 1, -1)
    data.dropna(inplace=True)

    # Triple Barrier Labeling (1 = Profit Target Hit First, 0 = Stop Loss or Time Expiry)
    closes = data["close"].to_numpy()
    signals = data["signal"].to_numpy()
    labels = np.zeros(len(data), dtype=int)

    for i in range(len(data) - max_hold):
        entry = closes[i]
        sig = signals[i]
        upper = entry * (1 + pt) if sig > 0 else entry * (1 - pt)
        lower = entry * (1 - sl) if sig > 0 else entry * (1 + sl)

        for j in range(i + 1, i + 1 + max_hold):
            price = closes[j]
            if (sig > 0 and price >= upper) or (sig < 0 and price <= upper):
                labels[i] = 1
                break
            if (sig > 0 and price <= lower) or (sig < 0 and price >= lower):
                labels[i] = 0
                break

    data["target"] = labels
    return data.iloc[:-max_hold]


def train_and_export_meta_model(symbol: str = "SPY") -> None:
    cfg = QuantConfig()
    df_raw = DataLayer(cfg).get_bars(symbol=symbol, days=730)
    dataset = build_features_and_triple_barrier(df_raw)

    X = dataset[FEATURE_COLS]
    y = dataset["target"]

    # Purged Time-Series Cross-Validation (gap=10 bars prevents overlapping label leakage)
    tscv = TimeSeriesSplit(n_splits=4, gap=10)
    precisions = []

    model = RandomForestClassifier(
        n_estimators=200, max_depth=5, min_samples_leaf=10, random_state=42
    )

    for train_idx, test_idx in tscv.split(X):
        model.fit(X.iloc[train_idx], y.iloc[train_idx])
        preds = model.predict(X.iloc[test_idx])
        precisions.append(precision_score(y.iloc[test_idx], preds, zero_division=0))

    # Fit on full dataset and serialize
    model.fit(X, y)
    model_path = ROOT_DIR / "models" / "rf_meta_model.pkl"
    joblib.dump(model, model_path)

    print(f"✅ Meta-Labeler Trained on {len(X)} events | Mean CV Precision: {np.mean(precisions):.2%}")
    print(f"📦 Saved serialized model to: {model_path}")


if __name__ == "__main__":
    train_and_export_meta_model("SPY")