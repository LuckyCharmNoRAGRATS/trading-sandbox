import numpy as np
import pandas as pd
from typing import Optional
from quant_engine import RegimeAnalyzer

class MetaLabelFilter:
    def __init__(self, confidence_threshold: float = 0.60):
        self.confidence_threshold = confidence_threshold

    @staticmethod
    def generate_features(df: pd.DataFrame) -> pd.DataFrame:
        """
        Generates strictly causal rolling features for the ML model.
        Every metric must be lagged or rolling to prevent look-ahead bias.
        """
        data = pd.DataFrame(index=df.index)
        
        # 1. Price Returns (Momentum)
        data['returns_1d'] = df['close'].pct_change(1)
        data['returns_5d'] = df['close'].pct_change(5)
        
        # 2. Volatility
        data['volatility_20d'] = df['close'].pct_change().rolling(20).std() * np.sqrt(252)
        
        # 3. Moving Average Convergence
        sma_20 = df['close'].rolling(20).mean()
        data['price_to_sma'] = (df['close'] - sma_20) / sma_20
        
        # 4. Regime Diagnostics (Rolling Hurst via Log Prices)
        data['hurst'] = RegimeAnalyzer.rolling_hurst(df['close'], window=60, max_lag=20)
        
        # Shift all features forward by 1 bar to ensure strict causality at the time of prediction
        data = data.shift(1).dropna()
        
        return data