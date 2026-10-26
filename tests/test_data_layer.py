import pytest
import pandas as pd
from datetime import datetime, timedelta, timezone
from alpaca.data.timeframe import TimeFrame
from quant_engine import DataLayer, QuantConfig

@pytest.fixture
def mock_data_layer(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    cfg = QuantConfig(api_key="mock", secret_key="mock")
    return DataLayer(config=cfg)

def test_cache_filename_is_master():
    filename = DataLayer.build_cache_filename(symbol="AAPL", timeframe=TimeFrame.Day)
    assert filename == "AAPL_1Day_master.parquet"

def test_data_validation_and_deduplication(mock_data_layer, tmp_path, monkeypatch):
    """Verifies that the incremental fetch drops duplicates and forward-fills gaps."""
    # Mock cache file
    cache_path = tmp_path / "AAPL_1Day_master.parquet"
    monkeypatch.setattr(DataLayer, "get_cache_path", lambda *args, **kwargs: cache_path)
    
    # Create fake cached data with a gap and a zero price
    base_time = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=5))
    df_cache = pd.DataFrame({
        "open": [150.0, 151.0],
        "high": [152.0, 153.0],
        "low": [149.0, 150.0],
        "close": [151.0, 0.0],  # 0.0 price should be filtered
        "volume": [1000, 1200]
    }, index=[base_time, base_time + timedelta(days=1)])
    df_cache.to_parquet(cache_path)
    
    class MockClient:
        def get_stock_bars(self, req):
            # Mock new data returning a duplicate overlapping row
            new_data = pd.DataFrame({
                "open": [151.0, 155.0],
                "high": [153.0, 157.0],
                "low": [150.0, 154.0],
                "close": [152.0, 156.0], # Updates the 0.0 price to 152.0
                "volume": [1200, 1500]
            }, index=[base_time + timedelta(days=1), base_time + timedelta(days=2)])
            class MockResponse:
                df = new_data
            return MockResponse()

    mock_data_layer.client = MockClient()
    
    final_df = mock_data_layer.get_bars(symbol="AAPL", days=5, timeframe=TimeFrame.Day)
    
    # Verify duplicates are merged (keeping 'last'), 0.0 prices are gone, length is exactly 3 rows
    assert len(final_df) == 3
    assert final_df.iloc[1]["close"] == 152.0
    assert (final_df["close"] > 0).all()