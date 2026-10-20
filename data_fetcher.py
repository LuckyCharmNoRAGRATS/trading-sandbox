import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pandas as pd
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

# 1. Environment & Path Setup
ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def get_historical_bars(
    symbol: str = "SPY",
    days: int = 252,
    timeframe: TimeFrame = TimeFrame.Day,
    force_refresh: bool = False
) -> pd.DataFrame:
    """
    Fetches historical stock bars. Loads from local disk cache if available;
    otherwise queries the API and caches the result.
    """
    file_path = DATA_DIR / f"{symbol}_{timeframe.value}_{days}d.parquet"

    # Return cached data if available
    if file_path.exists() and not force_refresh:
        return pd.read_parquet(file_path)

    if not API_KEY or not SECRET_KEY:
        raise ValueError("Missing API credentials. Check your .env file.")

    client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
    
    # Free tier safe window: 15-minute offset + UTC
    end_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    start_time = end_time - timedelta(days=days)

    params = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=timeframe,
        start=start_time,
        end=end_time,
        feed=DataFeed.IEX
    )

    bars = client.get_stock_bars(params)
    df = bars.df

    if df.empty:
        raise ValueError(f"No market data returned for {symbol}. Verify feed settings and market hours.")

    # Flatten Alpaca's multi-index and clean columns
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol")

    # Cache locally in Parquet format for fast I/O
    df.to_parquet(file_path)
    return df


if __name__ == "__main__":
    ticker = "SPY"
    print(f"Fetching and caching data for {ticker}...")
    data = get_historical_bars(symbol=ticker, days=180, force_refresh=True)
    print(f"✅ Success! Cached {len(data)} bars to /data folder.")
    print(data.tail())