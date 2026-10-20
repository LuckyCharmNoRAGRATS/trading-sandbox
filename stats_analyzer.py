import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from statsmodels.tsa.stattools import adfuller

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from pathlib import Path
from dotenv import load_dotenv
from data_fetcher import get_historical_bars
from statsmodels.tsa.stattools import adfuller
import numpy as np

# Load data directly from cache
df = get_historical_bars(symbol="SPY", days=180)
close_prices = df["close"].dropna().values

# Run stationarity check
adf_res = adfuller(close_prices)
print(f"ADF Statistic: {adf_res[0]:.4f}, p-value: {adf_res[1]:.4f}")

env_path = Path('C:/Users/HP/trading-sandbox/.env')
load_dotenv(dotenv_path=env_path)
# Load keys
load_dotenv()
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

def get_hurst_exponent(time_series, max_lag=20):
    """
    Calculates the Hurst Exponent (H) of a time series.
    H < 0.5: Mean Reverting
    H == 0.5: Random Walk
    H > 0.5: Trending / Momentum
    """
    lags = range(2, max_lag)
    # Calculate the array of standard deviations of the differenced series
    tau = [np.std(np.subtract(time_series[lag:], time_series[:-lag])) for lag in lags]
    # Perform a linear regression to find the slope
    reg = np.polyfit(np.log(lags), np.log(tau), 1)
    return reg[0]

def main():
    # 1. Initialize Client
    data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

    # 2. Define Request Parameters
    # We pull 60 days of daily data for statistical significance
    end_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    start_time = end_time - timedelta(days=60)
    symbol = "SPY" 

    print(f"Fetching data for {symbol}...")
    request_params = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Day,
        start=start_time,
        end=end_time,
        feed=DataFeed.IEX
    )

    # 3. Fetch Data
    bars = data_client.get_stock_bars(request_params)
    if bars.df.empty:
        print("Dataframe is empty. Check your market hours or API keys.")
        return
        
    df = bars.df

    # 4. Data Processing
    # Extract closing prices from Alpaca's MultiIndex DataFrame
    close_prices = df.loc[symbol]['close'].values
    
    # Compute Log Returns
    log_returns = np.log(close_prices[1:] / close_prices[:-1])

    # 5. Statistical Tests
    print("\n" + "="*45)
    print(f"STATISTICAL ANALYSIS FOR {symbol}")
    print("="*45)

    # A. Augmented Dickey-Fuller Test on Raw Prices
    adf_prices = adfuller(close_prices)
    print(f"\n1. Raw Price ADF p-value: {adf_prices[1]:.4f}")
    if adf_prices[1] > 0.05:
        print("   Result: > 0.05. Prices are non-stationary (Expected).")
    else:
        print("   Result: < 0.05. Prices are stationary (Unusual).")

    # B. Augmented Dickey-Fuller Test on Log Returns
    adf_returns = adfuller(log_returns)
    print(f"\n2. Log Return ADF p-value: {adf_returns[1]:.4f}")
    if adf_returns[1] < 0.05:
        print("   Result: < 0.05. Returns are stationary (Suitable for modeling).")
    else:
        print("   Result: > 0.05. Returns are non-stationary.")

    # C. Hurst Exponent
    hurst_exp = get_hurst_exponent(close_prices)
    print(f"\n3. Hurst Exponent (H): {hurst_exp:.4f}")
    if hurst_exp < 0.5:
        print("   Result: Mean-reverting candidate.")
    elif hurst_exp > 0.5:
        print("   Result: Momentum / Trending candidate.")
    else:
        print("   Result: Random Walk.")
        
    print("="*45 + "\n")

if __name__ == "__main__":
    main()