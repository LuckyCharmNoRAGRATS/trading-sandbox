import os
import sys
import time
import math
import logging
import joblib
from pathlib import Path
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from risk_manager import RiskManager
import dashboard_bridge as dash

# 1. Setup Self-Healing Paths & Logging
ROOT_DIR = Path(__file__).resolve().parent
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / "trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.getLogger().addHandler(dash.DashboardLogHandler())

load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    print("❌ CRITICAL ERROR: Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
    sys.exit(1)

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# 2. Load the AI Meta-Labeler
MODEL_PATH = ROOT_DIR / "models" / "rf_meta_model.pkl"
try:
    meta_model = joblib.load(MODEL_PATH)
    logging.info("✅ AI Meta-Labeler loaded successfully.")
except Exception as e:
    logging.warning(f"⚠️ AI Model not found. Running base strategy only. Error: {e}")
    meta_model = None

# 3. Live Editable Sandbox State
STATE = {
    "paused": False,
    "watchlist": ["AAPL", "SPY", "QQQ", "MSFT", "NVDA"], # Expanded Institutional Watchlist
    "enabled_tickers": {"AAPL": True, "SPY": True, "QQQ": True, "MSFT": True, "NVDA": True},
    "params": {
        "max_risk_per_trade": 0.01,
        "max_position_pct": 0.05,
        "max_daily_drawdown": 0.03,
        "fast_sma": 20,       # Replaces bb_window
        "slow_sma": 40,       # The winning long window
        "stop_loss_pct": 0.01, # The winning 1.0% stop-loss
        "ai_confidence_min": 0.60,
        "poll_seconds": 60
    }
}

def sync_alpaca_telemetry():
    """Pulls real-time Account, Open Positions, and Orders from Alpaca and pushes to Supabase."""
    account = trading_client.get_account()
    equity = float(account.equity)
    last_equity = float(account.last_equity)
    bp = float(account.buying_power)
    daily_pnl = equity - last_equity

    dash.set_kv("account", {
        "status": str(account.status).replace("AccountStatus.", ""),
        "buying_power": round(bp, 2),
        "equity": round(equity, 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round((daily_pnl / last_equity) * 100, 2) if last_equity else 0.0,
        "currency": account.currency,
        "paused": STATE["paused"]
    })

    dash.set_kv("node", {
        "status": "PAUSED" if STATE["paused"] else "Online",
        "host": "GCP VM · tmux (Alpaca Paper Connected)"
    })

    raw_positions = trading_client.get_all_positions()
    positions_list = [{
        "symbol": p.symbol,
        "qty": float(p.qty),
        "side": str(p.side).replace("PositionSide.", ""),
        "avg_entry_price": round(float(p.avg_entry_price), 2),
        "current_price": round(float(p.current_price), 2),
        "market_value": round(float(p.market_value), 2),
        "unrealized_pl": round(float(p.unrealized_pl), 2),
        "unrealized_plpc": round(float(p.unrealized_plpc) * 100, 2)
    } for p in raw_positions]
    dash.set_kv("alpaca_positions", {"positions": positions_list})

    orders_req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=25)
    raw_orders = trading_client.get_orders(filter=orders_req)
    orders_list = [{
        "id": str(o.id)[:8],
        "full_id": str(o.id),
        "symbol": o.symbol,
        "side": str(o.side).replace("OrderSide.", ""),
        "qty": float(o.qty) if o.qty else 0,
        "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else 0.0,
        "status": str(o.status).replace("OrderStatus.", ""),
        "type": str(o.order_type).replace("OrderType.", ""),
        "created_at": o.created_at.isoformat() if o.created_at else ""
    } for o in raw_orders]
    dash.set_kv("alpaca_orders", {"orders": orders_list})
    dash.set_kv("risk", STATE["params"])
    return account

# 4. Command Handlers Triggered from Phone or Laptop Web UI
def handle_submit_order(args: dict) -> str:
    symbol = str(args.get("symbol", "AAPL")).upper().strip()
    side_str = str(args.get("side", "BUY")).upper()
    side = OrderSide.BUY if "BUY" in side_str else OrderSide.SELL
    order_type = str(args.get("order_type", "MARKET")).upper()
    strategy_tag = str(args.get("strategy", "Manual Web/Mobile Ticket"))

    account = trading_client.get_account()
    bp = float(account.buying_power)
    entry_price = float(args.get("entry_price", 0) or 0)
    stop_loss = float(args.get("stop_loss", 0) or 0)
    qty = int(float(args.get("qty", 0) or 0))

    if qty <= 0 and entry_price > 0 and stop_loss > 0:
        rm = RiskManager(account_balance=bp, max_risk_per_trade=STATE["params"]["max_risk_per_trade"])
        qty = rm.calculate_position_size(entry_price=entry_price, stop_loss_price=stop_loss)

    if qty <= 0:
        raise ValueError("Quantity is 0. Provide a positive qty or valid entry_price & stop_loss.")

    if order_type == "LIMIT" and entry_price > 0:
        req = LimitOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY, limit_price=round(entry_price, 2))
    else:
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY)

    order = trading_client.submit_order(req)

    dash.push_trade_row({
        "id": str(order.id)[:8],
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": symbol,
        "strategy": strategy_tag,
        "regime": "LIVE_ALPACA_OMS",
        "side": side.value.upper(),
        "qty": qty,
        "entry": entry_price,
        "exit": entry_price,
        "z_score": float(args.get("z_score", 0.0)),
        "ai_conf": float(args.get("ai_conf", 0.0)),
        "slippage_bps": 4.0,
        "pnl": 0.0,
        "status": str(order.status).replace("OrderStatus.", ""),
        "notes": str(args.get("notes", "Routed via Web/Mobile Alpaca Terminal"))
    })
    sync_alpaca_telemetry()
    return f"Submitted {side.value.upper()} {qty} {symbol}"

def handle_close_position(args: dict) -> str:
    symbol = str(args.get("symbol", "")).upper().strip()
    trading_client.close_position(symbol)
    sync_alpaca_telemetry()
    return f"Closed open Alpaca position for {symbol}"

def handle_cancel_all(args: dict) -> str:
    STATE["paused"] = True
    trading_client.cancel_orders()
    trading_client.close_all_positions(cancel_orders=True)
    sync_alpaca_telemetry()
    return "EMERGENCY KILL SWITCH: Canceled all open orders and paused bot."

dash.serve_commands({
    "submit_order":    handle_submit_order,
    "close_position":  handle_close_position,
    "cancelall":       handle_cancel_all,
    "sync_alpaca":     lambda a: sync_alpaca_telemetry() and "Synced",
})

def evaluate_ticker(symbol: str, account):
    end_time = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_time = end_time - timedelta(days=90)

    req = StockBarsRequest(symbol_or_symbols=[symbol], timeframe=TimeFrame.Day, start=start_time, end=end_time, feed=DataFeed.IEX)
    bars = data_client.get_stock_bars(req).df
    if bars.empty: return
    if isinstance(bars.index, pd.MultiIndex): bars = bars.xs(symbol, level="symbol")

    closes = bars["close"].dropna()
    if len(closes) < 21: return # Not enough data for indicators

    w = int(STATE["params"]["bb_window"])
    sma = float(closes.rolling(w).mean().iloc[-1])
    std = float(closes.rolling(w).std().iloc[-1])
    current_close = float(closes.iloc[-1])
    z_score = (current_close - sma) / std if std > 0 else 0.0

    lags = range(2, 20)
    tau = [np.std(np.subtract(closes.values[lag:], closes.values[:-lag])) for lag in lags]
    hurst = float(np.polyfit(np.log(lags), np.log(tau), 1)[0])

    z_thresh = STATE["params"]["z_entry_threshold"]
    enabled = STATE["enabled_tickers"].get(symbol, True)

    status = "NO SIGNAL"
    if not enabled or STATE["paused"]: status = "PAUSED"
    elif abs(z_score) >= z_thresh: status = "ACTIVE"
    elif abs(z_score) >= z_thresh * 0.8: status = "WATCHING"

    dash.set_signal(
        ticker=symbol,
        strategy=f"Bollinger Mean Reversion ({w}d)",
        status=status,
        metrics={"close": current_close, "z_score": round(z_score, 3), "hurst": round(hurst, 3)},
        enabled=enabled
    )

    # Autonomous Paper Execution with AI Veto Filter
    if enabled and not STATE["paused"] and abs(z_score) >= z_thresh and hurst < 0.50:
        open_syms = {p.symbol for p in trading_client.get_all_positions()}
        if symbol not in open_syms:
            side = "SELL" if z_score > 0 else "BUY"
            stop_dist = current_close * STATE["params"]["stop_loss_pct"]
            stop_px = current_close + stop_dist if side == "SELL" else current_close - stop_dist
            
            ai_probability = 1.0 # Default if no model
            if meta_model:
                # Calculate Live ML Features
                returns_1d = float(closes.pct_change(1).iloc[-1])
                returns_5d = float(closes.pct_change(5).iloc[-1])
                vol_20d = float(closes.pct_change(1).rolling(20).std().iloc[-1] * np.sqrt(252))
                
                live_features = pd.DataFrame([{
                    'returns_1d': returns_1d,
                    'returns_5d': returns_5d,
                    'volatility_20d': vol_20d,
                    'price_to_sma': current_close / sma,
                    'hurst': hurst
                }])
                
                if not live_features.isna().any().any():
                    ai_probability = meta_model.predict_proba(live_features)[0][1]
            
            # The AI Circuit Breaker
            min_conf = STATE["params"]["ai_confidence_min"]
            if ai_probability < min_conf:
                msg = f"🛑 AI VETO: {symbol} trade aborted. Confidence ({ai_probability:.2%}) < {min_conf:.2%}"
                logging.info(msg)
                dash.notify(msg) # Push veto alert to Discord
                return

            # If AI approves, submit trade
            handle_submit_order({
                "symbol": symbol,
                "side": side,
                "entry_price": current_close,
                "stop_loss": stop_px,
                "z_score": round(z_score, 2),
                "ai_conf": ai_probability,
                "strategy": "AI Meta-Label Pipeline",
                "notes": f"Auto-triggered Z={z_score:.2f}, H={hurst:.2f}, AI={ai_probability:.2%}"
            })
            dash.notify(f"🟢 ML-APPROVED TRADE ROUTED: {side} {symbol} @ {current_close:.2f} (AI Confidence: {ai_probability:.2%})")

def run_bot():
    print("🤖 Launching AI-Filtered Alpaca <-> Supabase Multi-Ticker Scanner...")
    dash.notify("✅ System Online: Scanning Multi-Ticker Universe with AI Oversight.")
    
    while True:
        try:
            account = sync_alpaca_telemetry()
            for sym in list(STATE["watchlist"]):
                evaluate_ticker(sym, account)
        except Exception as e:
            logging.error(f"Main loop exception: {e}")
            dash.notify(f"❌ System Error: {e}")
        time.sleep(int(STATE["params"].get("poll_seconds", 60)))

if __name__ == "__main__":
    run_bot()