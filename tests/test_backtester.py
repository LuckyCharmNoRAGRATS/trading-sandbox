import pytest
import pandas as pd
import numpy as np
from quant_engine import FrictionBacktester, QuantConfig

class AlwaysLongStrategy:
    """Dummy strategy that always stays long for testing exposure and returns."""
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(1, index=df.index)

class AlternatingStrategy:
    """Dummy strategy that flips position every bar to test maximum friction deduction."""
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = [1 if i % 2 == 0 else -1 for i in range(len(df))]
        return pd.Series(signals, index=df.index)

@pytest.fixture
def dummy_price_data():
    # Simple linear uptrend: 100 -> 101 -> 102 -> 103 -> 104 -> 105
    return pd.DataFrame({"close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]})

def test_backtester_metrics_generation(dummy_price_data):
    config = QuantConfig(spread_slippage_bps=0.000) # Zero friction for pure return check
    backtester = FrictionBacktester(config)
    
    res = backtester.run(dummy_price_data, AlwaysLongStrategy())
    
    # Verify the expanded institutional metrics are present
    assert "Sortino_Ratio" in res
    assert "Calmar_Ratio" in res
    assert "Profit_Factor" in res
    assert "Market_Exposure_Pct" in res
    
    # In a constant uptrend with a long strategy, win rate and exposure should be 100%
    assert res["Market_Exposure_Pct"] == 100.0
    assert res["Win_Rate_Pct"] == 100.0
    assert res["Max_Drawdown_Pct"] == 0.0

def test_backtester_friction_deduction(dummy_price_data):
    # Set severe friction (1% per turnover) to ensure deduction is mathematically correct
    config = QuantConfig(spread_slippage_bps=0.01) 
    backtester = FrictionBacktester(config)
    
    res = backtester.run(dummy_price_data, AlternatingStrategy())
    
    # Heavy turnover combined with severe friction should result in negative total return
    assert res["Total_Return_Pct"] < 0.0
    assert res["Trade_Frequency"] > 0.0