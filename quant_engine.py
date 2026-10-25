import os
import sys
import math
import time
import logging
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, Optional

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from statsmodels.tsa.stattools import adfuller

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

# 1. Environment, Directories & Self-Healing Logging
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)

logging.basicConfig(
    filename=LOG_DIR / "trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


@dataclass(frozen=True)
class QuantConfig:
    api_key: str = os.getenv("ALPACA_API_KEY", "")
    secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    discord_webhook: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    max_risk_per_trade: float = float(os.getenv("MAX_RISK_PER_TRADE", 0.01))
    max_position_pct: float = float(os.getenv("MAX_POSITION_SIZE", 0.05))
    max_daily_drawdown: float = float(os.getenv("MAX_DAILY_DRAWDOWN", 0.03))
    spread_slippage_bps: float = 0.0008  # 8 bps round-trip friction


# 2. Decoupled Data Layer (Parquet Cache + IEX Feed)
class DataLayer:
    def __init__(self, config: QuantConfig):
        if not config.api_key or not config.secret_key:
            raise ValueError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
        self.client = StockHistoricalDataClient(config.api_key, config.secret_key)

    def get_bars(
        self,
        symbol: str = "SPY",
        days: int = 252,
        timeframe: TimeFrame = TimeFrame.Day,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        cache_file = DATA_DIR / f"{symbol}_{timeframe.value}_{days}d.parquet"
        if cache_file.exists() and not force_refresh:
            return pd.read_parquet(cache_file)

        end_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        start_time = end_time - timedelta(days=days)

        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=timeframe,
            start=start_time,
            end=end_time,
            feed=DataFeed.IEX,
        )
        df = self.client.get_stock_bars(req).df
        if df.empty:
            raise ValueError(f"Empty DataFrame returned for {symbol}.")

        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")

        df.to_parquet(cache_file)
        return df


# 3. Econometric Regime & Statistical Analyzer
class RegimeAnalyzer:
    @staticmethod
    def hurst_exponent(series: np.ndarray, max_lag: int = 20) -> float:
        lags = range(2, max_lag)
        tau = [np.std(np.subtract(series[lag:], series[:-lag])) for lag in lags]
        reg = np.polyfit(np.log(lags), np.log(tau), 1)
        return float(reg[0])

    @classmethod
    def evaluate(cls, close_prices: pd.Series) -> Dict[str, float | str]:
        prices = close_prices.dropna().values
        log_returns = np.log(prices[1:] / prices[:-1])

        adf_price_p = float(adfuller(prices)[1])
        adf_ret_p = float(adfuller(log_returns)[1])
        hurst = cls.hurst_exponent(prices)

        if hurst < 0.48:
            regime = "MEAN_REVERTING"
        elif hurst > 0.52:
            regime = "TRENDING"
        else:
            regime = "RANDOM_WALK"

        return {
            "adf_price_pvalue": round(adf_price_p, 4),
            "adf_return_pvalue": round(adf_ret_p, 4),
            "hurst_exponent": round(hurst, 4),
            "regime": regime,
        }


# 4. Institutional Risk & Capital Allocation Engine
class RiskEngine:
    def __init__(self, account_equity: float, config: QuantConfig):
        self.equity = account_equity
        self.config = config

    @staticmethod
    def half_kelly_fraction(win_prob: float, win_loss_ratio: float) -> float:
        if win_loss_ratio <= 0:
            return 0.0
        full_kelly = win_prob - ((1.0 - win_prob) / win_loss_ratio)
        return max(0.0, 0.5 * full_kelly)

    def check_daily_circuit_breaker(self, start_of_day_equity: float) -> bool:
        if start_of_day_equity <= 0:
            return False
        drawdown = (start_of_day_equity - self.equity) / start_of_day_equity
        if drawdown >= self.config.max_daily_drawdown:
            logging.critical(f"CIRCUIT BREAKER TRIGGERED: Daily drawdown {drawdown:.2%} exceeds limit.")
            return False
        return True

    def size_position(self, entry_price: float, stop_loss_price: float) -> int:
        if entry_price <= 0 or stop_loss_price <= 0:
            raise ValueError("Prices must be positive.")
        risk_per_share = abs(entry_price - stop_loss_price)
        if risk_per_share == 0:
            return 0

        # 1% Stop-Loss Risk Cap
        risk_budget = self.equity * self.config.max_risk_per_trade
        shares_by_risk = math.floor(risk_budget / risk_per_share)

        # 5% Maximum Notional Concentration Cap
        max_notional = self.equity * self.config.max_position_pct
        shares_by_notional = math.floor(max_notional / entry_price)

        return max(0, min(shares_by_risk, shares_by_notional))


# 5. Friction-Adjusted Vectorized Backtester
class FrictionBacktester:
    def __init__(self, config: QuantConfig):
        self.friction = config.spread_slippage_bps

    def run_sma_crossover(
        self, df: pd.DataFrame, fast_window: int = 20, slow_window: int = 50
    ) -> Dict[str, float]:
        data = df[["close"]].copy()
        data["fast"] = data["close"].rolling(fast_window).mean()
        data["slow"] = data["close"].rolling(slow_window).mean()

        data["signal"] = np.where(data["fast"] > data["slow"], 1, -1)
        # Shift by 1 bar to eliminate look-ahead bias
        data["position"] = data["signal"].shift(1)
        data.dropna(inplace=True)

        # Deduct explicit spread/slippage cost on position turnover
        data["turnover"] = data["position"].diff().abs().fillna(0)
        data["market_ret"] = np.log(data["close"] / data["close"].shift(1)).fillna(0)
        data["strat_ret_net"] = (data["position"] * data["market_ret"]) - (
            data["turnover"] * self.friction
        )

        cum_mkt = np.exp(data["market_ret"].cumsum())
        cum_strat = np.exp(data["strat_ret_net"].cumsum())

        std = data["strat_ret_net"].std()
        sharpe = (data["strat_ret_net"].mean() / std) * np.sqrt(252) if std > 0 else 0.0

        peak = np.maximum.accumulate(cum_strat)
        max_dd = ((cum_strat - peak) / peak).min()
        active_days = (data["strat_ret_net"] != 0).sum()
        win_rate = (data["strat_ret_net"] > 0).sum() / active_days if active_days > 0 else 0.0

        return {
            "Benchmark_Return_Pct": round((cum_mkt.iloc[-1] - 1) * 100, 2),
            "Net_Strategy_Return_Pct": round((cum_strat.iloc[-1] - 1) * 100, 2),
            "Annualized_Sharpe": round(float(sharpe), 2),
            "Max_Drawdown_Pct": round(float(max_dd) * 100, 2),
            "Win_Rate_Pct": round(float(win_rate) * 100, 1),
        }
import joblib
import pandas as pd
from pathlib import Path

# 1. Load the Trained AI Model at the top of your script
ROOT_DIR = Path(__file__).resolve().parent
MODEL_PATH = ROOT_DIR / "models" / "rf_meta_model.pkl"

try:
    meta_model = joblib.load(MODEL_PATH)
    print("✅ AI Meta-Labeler loaded successfully.")
except FileNotFoundError:
    print("⚠️ Warning: AI Model not found. Running base statistical strategy only.")
    meta_model = None

# ... (Inside your evaluate_ticker or execute_trade function) ...

def evaluate_and_trade(symbol: str, current_features: dict):
    # Assume your base strategy (e.g., Z-Score) generated a BUY signal
    base_signal = True 
    
    if base_signal and meta_model:
        # 2. Format the live market data into the exact features the AI expects
        live_data = pd.DataFrame([{
            'returns_1d': current_features['returns_1d'],
            'returns_5d': current_features['returns_5d'],
            'volatility_20d': current_features['volatility_20d'],
            'price_to_sma': current_features['price_to_sma'],
            'hurst': current_features['hurst']
        }])
        
        # 3. Ask the AI for the probability of success
        ai_probability = meta_model.predict_proba(live_data)[0][1] # Probability of class 1 (Profit)
        
        print(f"🤖 AI Confidence Score for {symbol}: {ai_probability:.2%}")
        
        # 4. The AI Veto Filter
        if ai_probability < 0.60:
            print(f"🛑 AI VETO: Confidence ({ai_probability:.2%}) is below 60% threshold. Trade aborted.")
            return # Skip the trade
            
        print("🟢 AI APPROVED: Routing order to Alpaca...")
        # ... (Proceed to RiskManager sizing and Alpaca order submission) ...

# 6. Order Management & Webhook Execution Engine
class ExecutionOMS:
    def __init__(self, config: QuantConfig):
        self.config = config
        self.trading_client = TradingClient(config.api_key, config.secret_key, paper=True)

    def send_discord_alert(self, title: str, fields: list, is_buy: bool = True) -> None:
        if not self.config.discord_webhook:
            return
        payload = {
            "username": "Institutional Quant OMS",
            "embeds": [{
                "title": title,
                "color": 3066993 if is_buy else 15158332,
                "fields": fields,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        }
        try:
            requests.post(self.config.discord_webhook, json=payload, timeout=5).raise_for_status()
        except Exception as exc:
            logging.error(f"Webhook alert failed: {exc}")

    def execute_signal(
        self, symbol: str, entry_price: float, stop_loss: float, side: OrderSide
    ) -> Optional[str]:
        account = self.trading_client.get_account()
        if account.trading_blocked:
            logging.error("Account trading blocked by broker.")
            return None

        equity = float(account.equity)
        last_equity = float(account.last_equity)
        risk_engine = RiskEngine(account_equity=equity, config=self.config)

        if not risk_engine.check_daily_circuit_breaker(last_equity):
            return None

        qty = risk_engine.size_position(entry_price, stop_loss)
        if qty <= 0:
            logging.info(f"[{symbol}] Risk engine sized 0 shares. Order aborted.")
            return None

        order_req = MarketOrderRequest(
            symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY
        )
        order = self.trading_client.submit_order(order_req)
        logging.info(f"ORDER SUBMITTED | {side.value} {qty} {symbol} @ ~${entry_price:.2f} | ID: {order.id}")

        self.send_discord_alert(
            title=f"🚨 ORDER EXECUTED: {symbol}",
            is_buy=(side == OrderSide.BUY),
            fields=[
                {"name": "Action", "value": side.value.upper(), "inline": True},
                {"name": "Shares", "value": str(qty), "inline": True},
                {"name": "Est. Price", "value": f"${entry_price:.2f}", "inline": True},
                {"name": "Stop Loss", "value": f"${stop_loss:.2f}", "inline": True},
                {"name": "Order ID", "value": f"`{order.id}`", "inline": False},
            ],
        )
        return str(order.id)


if __name__ == "__main__":
    cfg = QuantConfig()
    data_layer = DataLayer(cfg)

    ticker = "SPY"
    bars_df = data_layer.get_bars(symbol=ticker, days=365)

    regime_stats = RegimeAnalyzer.evaluate(bars_df["close"])
    bt_metrics = FrictionBacktester(cfg).run_sma_crossover(bars_df, fast_window=20, slow_window=50)

    print(f"=== {ticker} INSTITUTIONAL DIAGNOSTICS ===")
    print("Regime Diagnostics:", regime_stats)
    print("Friction-Adjusted Backtest:", bt_metrics)

from discord_notifier import notify_error, notify_system_status
from alpaca.trading.client import TradingClient
import os

# Inside your main execution loop or startup sequence:
try:
    trading_client = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)
    account = trading_client.get_account()
    
    # Send daily buying power heartbeat to your phone
    notify_system_status(status_text=str(account.status), buying_power=float(account.buying_power))

except Exception as e:
    # If the API goes down or rate limits hit, instantly ping your phone
    notify_error(f"Alpaca Connection Failed: {e}")