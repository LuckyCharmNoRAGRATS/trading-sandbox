import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

import joblib
from pathlib import Path

class MetaLabelFilter:
    def __init__(self):
        # Dynamically locate the models folder
        model_path = Path(__file__).resolve().parent.parent / "models" / "rf_meta_model.pkl"
        
        if model_path.exists():
            self.model = joblib.load(model_path)
            print("✅ AI Meta-Labeler Loaded.")
        else:
            self.model = None
            print("⚠️ No trained model found. Operating in bypass mode.")

    @staticmethod
    def _calculate_hurst(series: np.ndarray, max_lag: int = 20) -> float:
        lags = range(2, max_lag)
        tau = [np.std(np.subtract(series[lag:], series[:-lag])) for lag in lags]
        reg = np.polyfit(np.log(lags), np.log(tau), 1)
        return float(reg[0])

    def generate_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates the market micro-structure features at the exact moment a signal is fired.
        """
        data = df.copy()
        
        # Volatility & Momentum Features
        data['returns_1d'] = data['close'].pct_change(1)
        data['returns_5d'] = data['close'].pct_change(5)
        data['volatility_20d'] = data['returns_1d'].rolling(20).std() * np.sqrt(252)
        
        # Econometric Regime Features
        data['sma_20'] = data['close'].rolling(20).mean()
        data['price_to_sma'] = data['close'] / data['sma_20']
        
        # Rolling Hurst Exponent (Memory of the market)
        data['hurst'] = data['close'].rolling(40).apply(self._calculate_hurst, raw=True)
        
        return data.dropna()

    def get_confidence_score(self, current_features: pd.Series) -> float:
        """
        Outputs the AI's probability (0.0 to 1.0) that the trade will hit the profit target.
        """
        if self.model is None:
            return 1.0  # Pass-through if ML model isn't trained yet
            
        # Strictly enforce the exact 5 features the Random Forest was trained on
        expected_features = ['returns_1d', 'returns_5d', 'volatility_20d', 'price_to_sma', 'hurst']
        model_input = current_features[expected_features].values.reshape(1, -1)
        
        probability = self.model.predict_proba(model_input)[0][1]
        return float(probability)