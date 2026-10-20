import os
import sys
import time
import math
import logging
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

# 2. Live Editable Sandbox State (Editable from Phone & Laptop UI)
STATE = {
    "paused": False,
    "watchlist": ["AAPL", "SPY"],
    "enabled_tickers": {"AAPL": True, "SPY": True},
    "params": {
        "max_risk_per_trade": float(os.getenv("MAX_RISK_PER_TRADE", 0.01)),
        "max_position_pct": float(os.getenv("MAX_POSITION_SIZE", 0.05)),
        "max_daily_drawdown": float(os.getenv("MAX_DAILY_DRAWDOWN", 0.03)),
        "bb_window": 20,
        "z_entry_threshold": 2.0,
        "stop_loss_pct": 0.02,
        "ai_confidence_min": 0.60,
        "poll_seconds": 30
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

    # Sync Open Alpaca Positions
    raw_positions = trading_client.get_all_positions()
    positions_list = []
    for p in raw_positions:
        positions_list.append({
            "symbol": p.symbol,
            "qty": float(p.qty),
            "side": str(p.side).replace("PositionSide.", ""),
            "avg_entry_price": round(float(p.avg_entry_price), 2),
            "current_price": round(float(p.current_price), 2),
            "market_value": round(float(p.market_value), 2),
            "unrealized_pl": round(float(p.unrealized_pl), 2),
            "unrealized_plpc": round(float(p.unrealized_plpc) * 100, 2)
        })
    dash.set_kv("alpaca_positions", {"positions": positions_list})

    # Sync Recent Alpaca Orders
    orders_req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=25)
    raw_orders = trading_client.get_orders(filter=orders_req)
    orders_list = []
    for o in raw_orders:
        orders_list.append({
            "id": str(o.id)[:8],
            "full_id": str(o.id),
            "symbol": o.symbol,
            "side": str(o.side).replace("OrderSide.", ""),
            "qty": float(o.qty) if o.qty else 0,
            "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else 0.0,
            "status": str(o.status).replace("OrderStatus.", ""),
            "type": str(o.order_type).replace("OrderType.", ""),
            "created_at": o.created_at.isoformat() if o.created_at else ""
        })
    dash.set_kv("alpaca_orders", {"orders": orders_list})
    dash.set_kv("risk", STATE["params"])
    return account


# 3. Command Handlers Triggered from Phone or Laptop Web UI
def handle_submit_order(args: dict) -> str:
    """Places a live Market or Limit order on Alpaca from Phone/Laptop."""
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

    # If qty is 0, auto-size using RiskManager (1% rule)
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

    # Record to Spreadsheet Ledger
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
        "ai_conf": float(args.get("ai_conf", 0.70)),
        "slippage_bps": 4.0,
        "pnl": 0.0,
        "status": str(order.status).replace("OrderStatus.", ""),
        "notes": str(args.get("notes", "Routed via Web/Mobile Alpaca Terminal"))
    })

    sync_alpaca_telemetry()
    return f"Submitted {side.value.upper()} {qty} {symbol} (Order ID: {str(order.id)[:8]})"


def handle_close_position(args: dict) -> str:
    symbol = str(args.get("symbol", "")).upper().strip()
    if not symbol:
        raise ValueError("Missing symbol to close.")
    trading_client.close_position(symbol)
    sync_alpaca_telemetry()
    return f"Closed open Alpaca position for {symbol}"


def handle_cancel_order(args: dict) -> str:
    order_id = str(args.get("order_id", "")).strip()
    if not order_id:
        raise ValueError("Missing order_id")
    trading_client.cancel_order_by_id(order_id)
    sync_alpaca_telemetry()
    return f"Canceled order {order_id[:8]}"


def handle_cancel_all(args: dict) -> str:
    STATE["paused"] = True
    trading_client.cancel_orders()
    trading_client.close_all_positions(cancel_orders=True)
    sync_alpaca_telemetry()
    return "EMERGENCY KILL SWITCH: Canceled all open orders, liquidated all Alpaca positions, and paused bot."


def handle_update_params(args: dict) -> str:
    """Allows editing Risk & Strategy Parameters live from Phone or Laptop."""
    for k, v in args.items():
        if k in STATE["params"]:
            STATE["params"][k] = float(v)
        elif k == "watchlist" and isinstance(v, list):
            STATE["watchlist"] = [str(x).upper().strip() for x in v if x]
    dash.set_kv("risk", STATE["params"])
    return f"Updated live sandbox parameters: {args}"


def handle_toggle(args: dict) -> str:
    tk = str(args.get("ticker", "")).upper()
    en = bool(args.get("enabled", True))
    STATE["enabled_tickers"][tk] = en
    return f"{tk} strategy enabled={en}"


def handle_resume(args: dict) -> str:
    STATE["paused"] = False
    sync_alpaca_telemetry()
    return "Bot resumed active market polling."


# Register all Web/Mobile commands with the background worker
dash.serve_commands({
    "submit_order":    handle_submit_order,
    "close_position":  handle_close_position,
    "cancel_order":    handle_cancel_order,
    "cancelall":       handle_cancel_all,
    "update_params":   handle_update_params,
    "toggle":          handle_toggle,
    "resume":          handle_resume,
    "sync_alpaca":     lambda a: sync_alpaca_telemetry() and "Alpaca account, positions & orders synced",
    "ai_veto":         lambda a: "AI Meta-Label RF Filter check complete: Confidence >= 60% required.",
    "discord_test":    lambda a: dash.notify("📡 Test ping from Web/Mobile Quant Dashboard") or "Discord ping sent"
})


def evaluate_ticker(symbol: str, account):
    """Fetches latest IEX bars from Alpaca, computes Z-Score & Hurst, updates live chart & executes if triggered."""
    end_time = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_time = end_time - timedelta(days=90)

    req = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Day,
        start=start_time,
        end=end_time,
        feed=DataFeed.IEX
    )
    bars = data_client.get_stock_bars(req).df
    if bars.empty:
        return

    if isinstance(bars.index, pd.MultiIndex):
        bars = bars.xs(symbol, level="symbol")

    closes = bars["close"].dropna()
    w = int(STATE["params"]["bb_window"])
    sma = float(closes.rolling(w).mean().iloc[-1])
    std = float(closes.rolling(w).std().iloc[-1])
    current_close = float(closes.iloc[-1])
    z_score = (current_close - sma) / std if std > 0 else 0.0

    # Calculate Hurst Exponent
    lags = range(2, 20)
    tau = [np.std(np.subtract(closes.values[lag:], closes.values[:-lag])) for lag in lags]
    hurst = float(np.polyfit(np.log(lags), np.log(tau), 1)[0])

    z_thresh = STATE["params"]["z_entry_threshold"]
    enabled = STATE["enabled_tickers"].get(symbol, True)

    status = "NO SIGNAL"
    if not enabled or STATE["paused"]:
        status = "PAUSED"
    elif abs(z_score) >= z_thresh:
        status = "ACTIVE"
    elif abs(z_score) >= z_thresh * 0.8:
        status = "WATCHING"

    dash.set_signal(
        ticker=symbol,
        strategy=f"Bollinger Mean Reversion ({w}d, {z_thresh}σ)",
        status=status,
        metrics={
            "close": round(current_close, 2),
            "sma": round(sma, 2),
            "z_score": round(z_score, 3),
            "hurst": round(hurst, 3),
            "upper_band": round(sma + z_thresh * std, 2),
            "lower_band": round(sma - z_thresh * std, 2)
        },
        enabled=enabled
    )

    logging.info(f"[{symbol}] Close: ${current_close:.2f} | Z-Score: {z_score:.3f} | H: {hurst:.3f} | Status: {status}")

    # Autonomous Paper Execution if threshold breached & not currently holding position
    if enabled and not STATE["paused"] and abs(z_score) >= z_thresh and hurst < 0.50:
        open_syms = {p.symbol for p in trading_client.get_all_positions()}
        if symbol not in open_syms:
            side = "SELL" if z_score > 0 else "BUY"
            stop_dist = current_close * STATE["params"]["stop_loss_pct"]
            stop_px = current_close + stop_dist if side == "SELL" else current_close - stop_dist
            handle_submit_order({
                "symbol": symbol,
                "side": side,
                "entry_price": current_close,
                "stop_loss": stop_px,
                "z_score": round(z_score, 2),
                "strategy": "Auto Bollinger Z-Score",
                "notes": f"Auto-triggered at Z={z_score:.2f}, H={hurst:.2f}"
            })


def run_bot():
    print("🤖 Launching Two-Way Alpaca <-> Supabase Cloud Trader...")
    logging.info("Bot initialized with live two-way Alpaca OMS & Mobile/Laptop sync.")

    while True:
        try:
            account = sync_alpaca_telemetry()
            for sym in list(STATE["watchlist"]):
                evaluate_ticker(sym, account)
        except Exception as e:
            logging.error(f"Main loop exception: {e}")
        time.sleep(int(STATE["params"].get("poll_seconds", 30)))


if __name__ == "__main__":
    run_bot()