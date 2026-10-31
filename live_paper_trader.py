import os
import time
import logging
import math
import re
from datetime import datetime, timezone
import pandas as pd
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from quant_engine import QuantConfig, RiskEngine, DataLayer, load_meta_model
from discord_notifier import notify_system_status, notify_error

# 1. Environment & Logging Setup
load_dotenv(override=True)
logging.basicConfig(
    filename="logs/trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

try:
    import dashboard_bridge as dash
    DASHBOARD_SYNC = True
except (ImportError, ModuleNotFoundError, KeyError) as e:
    dash = None
    DASHBOARD_SYNC = False
    logging.warning(f"Dashboard bridge offline ({e}). Running in offline/local mode.")

class LiveExecutionEngine:
    def __init__(self):
        self.config = QuantConfig()
        self.trading_client = TradingClient(self.config.api_key, self.config.secret_key, paper=True)
        self.data_layer = DataLayer(self.config)
        self.meta_model = load_meta_model()
        
        # State Persistence
        self.watchlist = ["SPY", "QQQ", "GLD", "TLT"]
        self.active_positions = {}
        
    def sync_portfolio_state(self) -> dict:
        """Pulls live Alpaca state to feed the RiskEngine and Dashboard."""
        account = self.trading_client.get_account()
        positions = self.trading_client.get_all_positions()
        orders = self.trading_client.get_orders()
        
        self.active_positions = {p.symbol: float(p.qty) for p in positions}
        pending_symbols = [o.symbol for o in orders]
        
        state = {
            "equity": float(account.equity),
            "start_of_day_equity": float(account.last_equity),
            "buying_power": float(account.buying_power),
            "open_symbols": list(self.active_positions.keys()),
            "pending_symbols": pending_symbols,
            "portfolio_exposure": sum(float(p.market_value) for p in positions)
        }
        
        if DASHBOARD_SYNC and dash is not None:
            dash.set_kv("portfolio_state", state)
            
        return state

    def generate_live_signals(self, symbol: str) -> dict:
        """Fetches latest data and returns strategy state (Entry, Exit, or Hold)."""
        df = self.data_layer.get_bars(symbol=symbol, days=60, force_refresh=True)
        if df.empty:
            return {"action": "HOLD"}
            
        close = df["close"].iloc[-1]
        sma_20 = df["close"].rolling(20).mean().iloc[-1]
        std_20 = df["close"].rolling(20).std().iloc[-1]
        
        if pd.isna(sma_20) or std_20 == 0:
            return {"action": "HOLD"}
            
        z_score = (close - sma_20) / std_20
        
        # Mean Reversion Logic
        if z_score <= -2.0:
            return {"action": "BUY", "price": close, "stop": close * 0.98, "z": z_score}
        elif z_score >= 2.0:
            return {"action": "SELL", "price": close, "stop": close * 1.02, "z": z_score}
        elif abs(z_score) < 0.5:
            return {"action": "EXIT", "price": close, "z": z_score}
            
        return {"action": "HOLD"}

    def execute_loop(self):
        logging.info("Live Execution Engine Started.")
        notify_system_status(status_text="ONLINE", buying_power=0.0)

        while True:
            try:
                state = self.sync_portfolio_state()
                # Initialize the risk gate before processing any manual command.
                risk_engine = RiskEngine(account_equity=state["equity"], config=self.config)
                # 0. Process Web Dashboard Manual Orders.
                # E1.2 repairs the existing wire contract; atomic claims and broker
                # idempotency are separate E1.3 release blockers.
                if DASHBOARD_SYNC and dash is not None and dash.bridge.client:
                    pending_cmds = dash.bridge.get_pending_commands()
                    for cmd in pending_cmds:
                        if cmd.get("cmd") != "submit_order":
                            continue  # Other command types have separate consumers.
                        try:
                            payload = cmd.get("args")
                            if not isinstance(payload, dict):
                                raise ValueError("Order args must be an object")
                            sym = payload.get("symbol")
                            if not isinstance(sym, str):
                                raise ValueError("Missing or invalid symbol")
                            sym = sym.strip().upper()
                            if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", sym):
                                raise ValueError("Invalid symbol")
                            raw_side = payload.get("side")
                            if not isinstance(raw_side, str) or raw_side.strip().upper() not in ("BUY", "SELL"):
                                raise ValueError("Invalid order side")
                            side = OrderSide(raw_side.strip().lower())
                            entry = float(payload["entry_price"])
                            stop = float(payload["stop_loss"])
                            if not math.isfinite(entry) or not math.isfinite(stop) or entry <= 0 or stop <= 0:
                                raise ValueError("Prices must be finite and positive")
                            if (side == OrderSide.BUY and stop >= entry) or (side == OrderSide.SELL and stop <= entry):
                                raise ValueError("Stop price incompatible with trade side")

                            approved_shares = risk_engine.validate_and_size_order(
                                symbol=sym, entry_price=entry, stop_loss_price=stop,
                                open_positions=state["open_symbols"], pending_orders=state["pending_symbols"],
                                current_portfolio_exposure=state["portfolio_exposure"], start_of_day_equity=state["start_of_day_equity"]
                            )
                            if approved_shares <= 0:
                                dash.bridge.mark_command_processed(cmd["id"], status="failed", message="Risk Engine rejected the manual order.")
                                continue
                            request = MarketOrderRequest(
                                symbol=sym, qty=approved_shares, side=side, time_in_force=TimeInForce.DAY
                            )
                            response = self.trading_client.submit_order(request)
                            # Broker acceptance is not a fill; do not mark as filled.
                            dash.bridge.mark_command_processed(
                                cmd["id"], status="completed",
                                message=f"Order accepted by broker: {response.id}"
                            )
                            logging.info("[%s] Manual order submitted, id=%s", sym, response.id)
                        except (ValueError, KeyError, TypeError, OverflowError) as exc:
                            logging.warning("Manual command %s rejected: %s", cmd.get("id"), exc)
                            dash.bridge.mark_command_processed(
                                cmd["id"], status="failed", message=f"Invalid order: {exc}"
                            )

                for symbol in self.watchlist:
                    signal = self.generate_live_signals(symbol)
                    
                    # 1. Automated Exit Logic
                    if signal["action"] == "EXIT" and symbol in state["open_symbols"]:
                        logging.info(f"[{symbol}] Mean reversion achieved (Z: {signal.get('z', 0):.2f}). Exiting position.")
                        self.trading_client.close_position(symbol)
                        continue
                        
                    # 2. Entry Logic
                    if signal["action"] in ["BUY", "SELL"]:
                        side = OrderSide.BUY if signal["action"] == "BUY" else OrderSide.SELL
                        
                        # AI Meta-Labeler Veto
                        if self.meta_model:
                            # Construct features matching training schema
                            features = pd.DataFrame([{
                                'returns_1d': 0.0, 'returns_5d': 0.0, 
                                'volatility_20d': 0.0, 'price_to_sma': 0.0, 'hurst': 0.0
                            }])
                            ai_prob = self.meta_model.predict_proba(features)[0][1]
                            if ai_prob < 0.60:
                                logging.info(f"[{symbol}] AI VETO: Confidence {ai_prob:.2%} < 60%.")
                                continue
                        
                        # Centralized Risk Gate
                        shares = risk_engine.validate_and_size_order(
                            symbol=symbol,
                            entry_price=signal["price"],
                            stop_loss_price=signal["stop"],
                            open_positions=state["open_symbols"],
                            pending_orders=state["pending_symbols"],
                            current_portfolio_exposure=state["portfolio_exposure"],
                            start_of_day_equity=state["start_of_day_equity"]
                        )
                        
                        if shares > 0:
                            order = MarketOrderRequest(
                                symbol=symbol, qty=shares, side=side, time_in_force=TimeInForce.DAY
                            )
                            self.trading_client.submit_order(order)
                            logging.info(f"[{symbol}] ORDER EXECUTED: {side.value} {shares} shares @ ~${signal['price']:.2f}")

            except Exception as e:
                logging.error(f"Execution loop exception: {e}")
                notify_error(f"Engine Crash: {e}")
            
            time.sleep(60)

if __name__ == "__main__":
    engine = LiveExecutionEngine()
    engine.execute_loop()