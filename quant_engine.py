import os
import re
import math
import itertools
import logging
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union

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
MODEL_DIR = ROOT_DIR / "models"
DEFAULT_MODEL_PATH = MODEL_DIR / "rf_meta_model.pkl"

for directory in (DATA_DIR, LOG_DIR, MODEL_DIR):
    directory.mkdir(parents=True, exist_ok=True)

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


# Lazy ML model cache (never loaded at import time)
_CACHED_META_MODEL: Any = None


def load_meta_model(model_path: Optional[Path] = None, force_reload: bool = False) -> Optional[Any]:
    """
    Lazily loads the scikit-learn meta-labeling model only when explicitly called.
    Handles missing files or unpickling errors gracefully without crashing callers.
    """
    global _CACHED_META_MODEL
    if _CACHED_META_MODEL is not None and not force_reload:
        return _CACHED_META_MODEL

    target_path = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
    if not target_path.exists():
        logging.warning(f"Meta-model file not found at {target_path}. Operating without ML filter.")
        return None

    try:
        import joblib
        _CACHED_META_MODEL = joblib.load(target_path)
        return _CACHED_META_MODEL
    except Exception as exc:
        logging.error(f"Failed to load meta-model from {target_path}: {exc}")
        return None


# 2. Decoupled Data Layer (Parquet Cache + IEX Feed)
from alpaca.data.requests import StockBarsRequest
from alpaca.data.enums import DataFeed, Adjustment

class DataLayer:
    def __init__(self, config: Optional[QuantConfig] = None):
        self.config = config or QuantConfig()
        if not self.config.api_key or not self.config.secret_key:
            raise ValueError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
        self.client = StockHistoricalDataClient(self.config.api_key, self.config.secret_key)

    @staticmethod
    def build_cache_filename(symbol: str, timeframe: Union[TimeFrame, str] = TimeFrame.Day) -> str:
        """Builds a deterministic, cross-platform cache filename without the 'days' variable so it can append."""
        raw_tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
        clean_symbol = re.sub(r"[^A-Za-z0-9._-]", "", str(symbol).strip().upper())
        clean_tf = re.sub(r"[^A-Za-z0-9._-]", "", str(raw_tf).strip())
        return f"{clean_symbol}_{clean_tf}_master.parquet"

    @classmethod
    def get_cache_path(cls, symbol: str, timeframe: Union[TimeFrame, str] = TimeFrame.Day) -> Path:
        return DATA_DIR / cls.build_cache_filename(symbol=symbol, timeframe=timeframe)

    def get_bars(
        self,
        symbol: str = "SPY",
        days: int = 252,
        timeframe: TimeFrame = TimeFrame.Day,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        cache_file = self.get_cache_path(symbol=symbol, timeframe=timeframe)
        end_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        start_time = end_time - timedelta(days=days)
        
        cached_df = pd.DataFrame()
        fetch_start = start_time

        # 1. Load Cache & Determine Delta
        if cache_file.exists() and not force_refresh:
            cached_df = pd.read_parquet(cache_file)
            if not cached_df.empty:
                last_cached_time = cached_df.index[-1]
                if last_cached_time.tzinfo is None:
                    last_cached_time = last_cached_time.tz_localize(timezone.utc)
                
                # If cache is reasonably up to date (within the timeframe), return sliced data
                if end_time - last_cached_time < timedelta(days=1 if timeframe == TimeFrame.Day else 0.05):
                    return cached_df[cached_df.index >= pd.Timestamp(start_time)].copy()
                
                # Otherwise, fetch only the missing delta
                fetch_start = last_cached_time + timedelta(minutes=1)

        # 2. Fetch Missing Data with Corporate Action Adjustments
        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=timeframe,
            start=fetch_start,
            end=end_time,
            feed=DataFeed.IEX,
            adjustment=Adjustment.ALL  # CRITICAL: Adjusts for splits and dividends
        )
        
        new_df = self.client.get_stock_bars(req).df
        
        if not new_df.empty:
            if isinstance(new_df.index, pd.MultiIndex):
                new_df = new_df.xs(symbol, level="symbol")
            
            # Combine, deduplicate, and sort
            combined_df = pd.concat([cached_df, new_df])
            combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
            combined_df.sort_index(inplace=True)
            
            # Forward-fill missing internal values and drop negative prices
            combined_df.ffill(inplace=True)
            combined_df = combined_df[combined_df['close'] > 0]
            
            combined_df.to_parquet(cache_file)
            cached_df = combined_df

        if cached_df.empty:
            raise ValueError(f"No data available for {symbol}.")

        # Return only the requested window
        return cached_df[cached_df.index >= pd.Timestamp(start_time)].copy()


# 3. Econometric Regime & Statistical Analyzer
class RegimeAnalyzer:
    @staticmethod
    def hurst_exponent(series: np.ndarray, max_lag: int = 20) -> float:
        lags = range(2, max_lag)
        tau = [np.std(np.subtract(series[lag:], series[:-lag])) for lag in lags]
        reg = np.polyfit(np.log(lags), np.log(tau), 1)
        return float(reg[0])

    @classmethod
    def evaluate(cls, close_prices: pd.Series) -> Dict[str, Union[float, str]]:
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
    def __init__(self, account_equity: float, config: Optional[QuantConfig] = None):
        self.equity = account_equity
        self.config = config or QuantConfig()

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
            logging.critical(
                f"CIRCUIT BREAKER TRIGGERED: Daily drawdown {drawdown:.2%} exceeds limit."
            )
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

    def calculate_position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """Backward-compatible alias matching RiskManager.calculate_position_size."""
        return self.size_position(entry_price=entry_price, stop_loss_price=stop_loss_price)


# 5. Friction-Adjusted Vectorized Backtester
class FrictionBacktester:
    def __init__(self, config: Optional[QuantConfig] = None):
        self.config = config or QuantConfig()
        self.friction = self.config.spread_slippage_bps

    def run_sma_crossover(
        self, df: pd.DataFrame, fast_window: int = 20, slow_window: int = 50
    ) -> Dict[str, float]:
        data = df[["close"]].copy()
        data["fast"] = data["close"].rolling(fast_window).mean()
        data["slow"] = data["close"].rolling(slow_window).mean()

        data["signal"] = np.where(data["fast"] > data["slow"], 1, -1)
        data["position"] = data["signal"].shift(1)
        data.dropna(inplace=True)

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
            "Benchmark_Return_Pct": round(float((cum_mkt.iloc[-1] - 1) * 100), 2),
            "Net_Strategy_Return_Pct": round(float((cum_strat.iloc[-1] - 1) * 100), 2),
            "Annualized_Sharpe": round(float(sharpe), 2),
            "Max_Drawdown_Pct": round(float(max_dd) * 100, 2),
            "Win_Rate_Pct": round(float(win_rate) * 100, 1),
        }

    def grid_search(
        self, df: pd.DataFrame, fast_list=(10, 15, 20), slow_list=(40, 50, 60)
    ) -> pd.DataFrame:
        records = [
            {"Fast_SMA": f, "Slow_SMA": s, **self.run_sma_crossover(df, f, s)}
            for f, s in itertools.product(fast_list, slow_list)
            if f < s
        ]
        return (
            pd.DataFrame(records)
            .sort_values(by="Annualized_Sharpe", ascending=False)
            .reset_index(drop=True)
        )


# 6. Order Management & Webhook Execution Engine
class ExecutionOMS:
    def __init__(self, config: Optional[QuantConfig] = None):
        self.config = config or QuantConfig()
        self.trading_client = TradingClient(
            self.config.api_key, self.config.secret_key, paper=True
        )

    def send_discord_alert(self, title: str, fields: list, is_buy: bool = True) -> None:
        if not self.config.discord_webhook:
            return
        payload = {
            "username": "Institutional Quant OMS",
            "embeds": [
                {
                    "title": title,
                    "color": 3066993 if is_buy else 15158332,
                    "fields": fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ],
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
        logging.info(
            f"ORDER SUBMITTED | {side.value} {qty} {symbol} @ ~${entry_price:.2f} | ID: {order.id}"
        )

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


def run_diagnostics(ticker: str = "SPY", days: int = 365) -> None:
    """Runs offline/cached econometric and backtest diagnostics from the CLI."""
    cfg = QuantConfig()
    data_layer = DataLayer(cfg)
    bars_df = data_layer.get_bars(symbol=ticker, days=days)

    regime_stats = RegimeAnalyzer.evaluate(bars_df["close"])
    bt_metrics = FrictionBacktester(cfg).run_sma_crossover(
        bars_df, fast_window=20, slow_window=50
    )

    print(f"=== {ticker} INSTITUTIONAL DIAGNOSTICS ===")
    print("Regime Diagnostics:", regime_stats)
    print("Friction-Adjusted Backtest:", bt_metrics)


if __name__ == "__main__":
    run_diagnostics()