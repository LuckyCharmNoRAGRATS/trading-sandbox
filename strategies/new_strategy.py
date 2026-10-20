import vectorbt as vbt
import numpy as np
import pandas as ch_pd

def run_strategy_backtest(ticker="BTC", start_date="2025-01-01"):
    # 1. Fetch historical OHLCV data using VectorBT's data wrapper
    data = vbt.YFData.download(ticker, start=start_date)
    close = data.get("Close")
    
    # 2. Define Technical Indicators (e.g., Fast & Slow Moving Averages)
    fast_ma = vbt.MA.run(close, window=20)
    slow_ma = vbt.MA.run(close, window=50)
    
    # 3. Generate Entry & Exit Signals
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)
    
    # 4. Run Vectorized Portfolio Simulation
    pf = vbt.Portfolio.from_signals(
        close, 
        entries=entries, 
        exits=exits, 
        init_cash=10000.0, 
        fees=0.001 # 0.1% commission assumption
    )
    
    return pf