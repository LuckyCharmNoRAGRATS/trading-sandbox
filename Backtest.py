import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

# 1. Load Keys (Using the bulletproof method from stats_analyzer)
env_path = Path('C:/Users/HP/trading-sandbox/.env')
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    print("❌ CRITICAL ERROR: Could not find API keys.")
    sys.exit(1)

# 2. Fetch 2 Years of Historical Data
client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
end_time = datetime.now(timezone.utc) - timedelta(minutes=15)
start_time = end_time - timedelta(days=730)
symbol = "SPY"

print(f"Fetching 2 years of daily data for {symbol}...")
req = StockBarsRequest(
    symbol_or_symbols=[symbol],
    timeframe=TimeFrame.Day,
    start=start_time,
    end=end_time,
    feed=DataFeed.IEX
)

df = client.get_stock_bars(req).df
if df.empty:
    print("❌ Dataframe is empty.")
    sys.exit(1)

# Clean multi-index
df = df.loc[symbol].copy()

# 3. Generate Trading Signals
short_window = 20
long_window = 50

# Calculate Moving Averages
df['SMA_20'] = df['close'].rolling(window=short_window).mean()
df['SMA_50'] = df['close'].rolling(window=long_window).mean()

# Signal = 1 (Buy/Long) when 20-day > 50-day
# Signal = -1 (Sell/Short) when 20-day < 50-day
df['Signal'] = np.where(df['SMA_20'] > df['SMA_50'], 1, -1)

# 4. Calculate Returns & Prevent Look-Ahead Bias
# We MUST shift the signal by 1. If the SMA crosses today, we can only buy at TOMORROW'S open/close. 
# Trading on the same day the signal is generated is a fatal backtesting error called "Look-Ahead Bias".
df['Position'] = df['Signal'].shift(1)

# Calculate Daily Market Returns (Logarithmic)
df['Market_Return'] = np.log(df['close'] / df['close'].shift(1))

# Calculate Strategy Returns (Market Return * Our Position)
df['Strategy_Return'] = df['Position'] * df['Market_Return']

# 5. Evaluate Performance
df.dropna(inplace=True) # Drop initial rows where SMA couldn't calculate

cumulative_market = np.exp(df['Market_Return'].cumsum())
cumulative_strategy = np.exp(df['Strategy_Return'].cumsum())

# Annualized Sharpe Ratio (Assumes 252 trading days)
sharpe_ratio = (df['Strategy_Return'].mean() / df['Strategy_Return'].std()) * np.sqrt(252)

print("\n" + "="*45)
print(f"BACKTEST RESULTS: {symbol} SMA 20/50 CROSSOVER")
print("="*45)
print(f"Total Market Buy & Hold Return: {(cumulative_market.iloc[-1] - 1) * 100:>7.2f}%")
print(f"Total Strategy Return:          {(cumulative_strategy.iloc[-1] - 1) * 100:>7.2f}%")
print(f"Strategy Sharpe Ratio:          {sharpe_ratio:>7.2f}")
print("="*45 + "\n")