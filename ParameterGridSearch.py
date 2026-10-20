import yfinance as yf
import pandas as pd
import numpy as np
import itertools

def load_market_data(ticker="AAPL", period="3y"):
    """Downloads historical daily bar data using yfinance."""
    print(f"Downloading {period} of data for {ticker} via yfinance...")
    df = yf.download(ticker, period=period, interval="1d", progress=False)
    
    # Flatten multi-index columns if returned by yfinance
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    return df

def run_parameter_grid_search(data):
    """
    Pure NumPy/Pandas grid search over windows and Z-entry thresholds.
    No scikit-learn dependencies required.
    """
    param_grid = {
        'window': [10, 15, 20, 30, 50],
        'z_entry': [-1.2, -1.5, -1.8, -2.0, -2.5],
        'z_exit': [0.0]
    }
    
    keys, values = zip(*param_grid.items())
    permutations_dicts = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    results = []
    
    for params in permutations_dicts:
        w = params['window']
        z_in = params['z_entry']
        z_out = params['z_exit']
        
        df = data.copy()
        df['SMA'] = df['Close'].rolling(w).mean()
        df['STD'] = df['Close'].rolling(w).std()
        df['Z'] = (df['Close'] - df['SMA']) / df['STD']
        
        # Signal Generation
        df['Signal'] = np.nan
        df.loc[df['Z'] < z_in, 'Signal'] = 1.0
        df.loc[df['Z'] >= z_out, 'Signal'] = 0.0
        df['Position'] = df['Signal'].ffill().fillna(0.0)
        
        # Calculate returns (shift 1 day to eliminate lookahead bias)
        df['Ret'] = df['Close'].pct_change()
        df['Strat_Ret'] = df['Position'].shift(1) * df['Ret']
        
        # Compute Sharpe & Cumulative Returns
        ann_mean = df['Strat_Ret'].mean() * 252
        ann_std = df['Strat_Ret'].std() * np.sqrt(252)
        sharpe = (ann_mean / ann_std) if ann_std != 0 else -99
        cum_ret = (1 + df['Strat_Ret'].fillna(0)).cumprod().iloc[-1] - 1
        
        results.append({
            'window': w,
            'z_entry': z_in,
            'sharpe': round(sharpe, 2),
            'total_return_%': round(cum_ret * 100, 2)
        })
        
    results_df = pd.DataFrame(results).sort_values(by='sharpe', ascending=False)
    return results_df

if __name__ == "__main__":
    df = load_market_data(ticker="AAPL", period="3y")
    grid_results = run_parameter_grid_search(df)
    
    print("\n=== Top 10 Grid Search Combinations (by Sharpe Ratio) ===")
    print(grid_results.head(10).to_string(index=False))