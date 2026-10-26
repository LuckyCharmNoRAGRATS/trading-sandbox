import itertools
import pandas as pd
from typing import Dict, Any

# Import our upgraded components from quant_engine
from quant_engine import DataLayer, QuantConfig, FrictionBacktester, SMACrossover

def run_walk_forward_optimization(symbol: str = "SPY", days: int = 730) -> None:
    """
    Executes a grid search over In-Sample data, selects the optimal parameters, 
    and validates them against unseen Out-of-Sample data.
    """
    print(f"--- FETCHING DATA FOR {symbol} ---")
    data_layer = DataLayer()
    try:
        # Fetching 2 years of data to ensure a robust OOS test
        df = data_layer.get_bars(symbol=symbol, days=days)
    except Exception as e:
        print(f"Data error: {e}")
        return

    if len(df) < 252:
        print("⚠️ Insufficient data for a robust walk-forward split. Minimum 252 bars required.")
        return

    # 1. Split Data: 70% In-Sample (Train) / 30% Out-of-Sample (Test)
    split_index = int(len(df) * 0.7)
    train_df = df.iloc[:split_index].copy()
    test_df = df.iloc[split_index:].copy()

    print(f"In-Sample (Train) Bars: {len(train_df)} | Out-of-Sample (Test) Bars: {len(test_df)}\n")

    # 2. Define Parameter Grid
    fast_windows = [10, 15, 20]
    slow_windows = [40, 50, 60]
    
    config = QuantConfig()
    backtester = FrictionBacktester(config)
    results = []

    print("--- 🔬 IN-SAMPLE OPTIMIZATION (TRAINING) ---")
    for fast, slow in itertools.product(fast_windows, slow_windows):
        if fast >= slow:
            continue
            
        strategy = SMACrossover(fast_window=fast, slow_window=slow)
        metrics = backtester.run(train_df, strategy)
        
        results.append({
            "Fast_SMA": fast,
            "Slow_SMA": slow,
            "IS_Sharpe": metrics["Annualized_Sharpe"],
            "IS_Return_Pct": metrics["Total_Return_Pct"],
            "IS_Max_DD_Pct": metrics["Max_Drawdown_Pct"]
        })

    # Rank by In-Sample Sharpe Ratio
    results_df = pd.DataFrame(results).sort_values(by="IS_Sharpe", ascending=False).reset_index(drop=True)
    print("Top 3 Parameter Combinations (In-Sample):")
    print(results_df.head(3).to_string(index=False))

    # 3. Out-of-Sample Validation
    best_params = results_df.iloc[0]
    best_fast = int(best_params["Fast_SMA"])
    best_slow = int(best_params["Slow_SMA"])
    is_sharpe = best_params["IS_Sharpe"]

    print(f"\n--- 🚀 OUT-OF-SAMPLE VALIDATION (TESTING) ---")
    print(f"Deploying optimal parameters to unseen data: Fast={best_fast}, Slow={best_slow}")

    best_strategy = SMACrossover(fast_window=best_fast, slow_window=best_slow)
    oos_metrics = backtester.run(test_df, best_strategy)
    
    oos_sharpe = oos_metrics["Annualized_Sharpe"]
    
    # Calculate Sharpe Degradation
    if is_sharpe > 0:
        degradation = ((oos_sharpe - is_sharpe) / is_sharpe) * 100
    else:
        degradation = 0.0

    print(f"OOS Sharpe Ratio:       {oos_sharpe:.2f}")
    print(f"Sharpe Degradation:     {degradation:.1f}% (A drop > 30% indicates severe overfitting)")
    print(f"OOS Total Return:       {oos_metrics['Total_Return_Pct']}%")
    print(f"OOS Benchmark Return:   {oos_metrics['Benchmark_Return_Pct']}%")
    print(f"OOS Max Drawdown:       {oos_metrics['Max_Drawdown_Pct']}%")
    print(f"OOS Win Rate:           {oos_metrics['Win_Rate_Pct']}%")

if __name__ == "__main__":
    run_walk_forward_optimization(symbol="SPY", days=730)