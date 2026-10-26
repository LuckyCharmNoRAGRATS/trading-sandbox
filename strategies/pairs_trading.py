import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller, coint
from typing import Dict, Tuple, Optional

class CointegratedPairs:
    def __init__(self, p_value_threshold: float = 0.05):
        self.p_value_threshold = p_value_threshold
        self.hedge_ratio: float = 0.0
        self.is_cointegrated: bool = False

    def test_cointegration(self, asset_y: pd.Series, asset_x: pd.Series) -> Dict[str, float]:
        """
        Tests if asset_y and asset_x are cointegrated using the Engle-Granger two-step method.
        Returns the hedge ratio and ADF p-value of the spread.
        """
        # Align series
        df = pd.concat([asset_y, asset_x], axis=1).dropna()
        y = np.log(df.iloc[:, 0])
        x = np.log(df.iloc[:, 1])

        # Step 1: Ordinary Least Squares (OLS) regression to find the hedge ratio
        x_const = sm.add_constant(x)
        ols_model = sm.OLS(y, x_const).fit()
        self.hedge_ratio = ols_model.params.iloc[1]

        # Step 2: ADF test on the residuals (the spread)
        spread = y - (self.hedge_ratio * x)
        adf_result = adfuller(spread)
        p_value = adfuller(spread)[1]
        
        # Verify via statsmodels built-in coint test as a secondary check
        coint_stat, coint_p, _ = coint(y, x)

        self.is_cointegrated = p_value < self.p_value_threshold

        return {
            "hedge_ratio": float(self.hedge_ratio),
            "spread_adf_pvalue": float(p_value),
            "coint_test_pvalue": float(coint_p),
            "is_cointegrated": float(self.is_cointegrated)
        }

    def generate_spread(self, asset_y: pd.Series, asset_x: pd.Series) -> pd.Series:
        """Calculates the synthetic spread using the trained hedge ratio."""
        if not self.is_cointegrated:
            raise ValueError("Assets are not cointegrated or model has not been fitted.")
            
        df = pd.concat([asset_y, asset_x], axis=1).dropna()
        y = np.log(df.iloc[:, 0])
        x = np.log(df.iloc[:, 1])
        
        return y - (self.hedge_ratio * x)

    def generate_signals(self, spread: pd.Series, z_threshold: float = 2.0, window: int = 20) -> pd.DataFrame:
        """
        Generates mean-reversion signals on the synthetic spread.
        1 = Go Long Spread (Buy Y, Short X)
        -1 = Go Short Spread (Short Y, Buy X)
        """
        sma = spread.rolling(window).mean()
        std = spread.rolling(window).std()
        z_score = (spread - sma) / std

        signals = pd.DataFrame(index=spread.index)
        signals['z_score'] = z_score
        
        # Base mean reversion logic
        signals['signal'] = 0
        signals.loc[z_score <= -z_threshold, 'signal'] = 1
        signals.loc[z_score >= z_threshold, 'signal'] = -1
        
        return signals

if __name__ == "__main__":
    # Diagnostic dry-run
    np.random.seed(42)
    # Generate a random walk (Asset X)
    x_returns = np.random.normal(0, 0.01, 1000)
    asset_x = pd.Series(np.exp(np.cumsum(x_returns)) * 100, name="AssetX")
    
    # Generate a cointegrated Asset Y (X + stationary noise)
    noise = np.random.normal(0, 0.05, 1000)
    asset_y = pd.Series(asset_x * np.exp(noise), name="AssetY")

    pair = CointegratedPairs()
    stats = pair.test_cointegration(asset_y, asset_x)
    
    print("=== COINTEGRATION DIAGNOSTICS ===")
    print(f"Hedge Ratio: {stats['hedge_ratio']:.4f}")
    print(f"Spread ADF p-value: {stats['spread_adf_pvalue']:.4f}")
    print(f"Is Cointegrated? {bool(stats['is_cointegrated'])}")