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

    def process_claimed_order(self, cmd, bridge, state, risk_engine):
        """Process ONE owned command, never retry after a possible broker submission.

        This requires the unapplied E1.3 schema. All broker calls use paper=True.
        """
        from alpaca.trading.requests import MarketOrderRequest
        if cmd.get("cmd") != "submit_order" or cmd.get("status") != "claimed":
            raise ValueError("Unclaimed or unsupported command")
        client_id = cmd.get("client_order_id")
        if not isinstance(client_id, str) or not re.fullmatch(r"e1-[1-9][0-9]*", client_id):
            raise ValueError("Missing stable broker order identifier")
        try:
            payload = cmd["args"]
            if not isinstance(payload, dict):
                raise ValueError("Invalid order arguments")
            symbol = payload["symbol"]
            if not isinstance(symbol, str):
                raise ValueError("Invalid symbol")
            symbol = symbol.strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol):
                raise ValueError("Invalid symbol")
            side_text = payload["side"]
            if not isinstance(side_text, str) or side_text.strip().upper() not in ("BUY", "SELL"):
                raise ValueError("Invalid side")
            side = OrderSide(side_text.strip().lower())
            entry, stop = float(payload["entry_price"]), float(payload["stop_loss"])
            if not all(math.isfinite(v) and v > 0 for v in (entry, stop)):
                raise ValueError("Prices must be finite and positive")
            if (side == OrderSide.BUY and stop >= entry) or (side == OrderSide.SELL and stop <= entry):
                raise ValueError("Stop inconsistent with side")
            shares = risk_engine.validate_and_size_order(
                symbol=symbol, entry_price=entry, stop_loss_price=stop,
                open_positions=state["open_symbols"], pending_orders=state["pending_symbols"],
                current_portfolio_exposure=state["portfolio_exposure"],
                start_of_day_equity=state["start_of_day_equity"],
            )
            if shares <= 0:
                raise ValueError("Risk gate rejected order")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            bridge.transition_claimed(cmd, "claimed", "failed", message=f"Rejected: {exc}")
            return None

        # This durable pre-submit transition MUST succeed before contacting broker.
        bridge.transition_claimed(cmd, "claimed", "submitting")
        request = MarketOrderRequest(
            symbol=symbol, qty=shares, side=side, time_in_force=TimeInForce.DAY,
            client_order_id=client_id,
        )
        try:
            accepted = self.trading_client.submit_order(request)
        except Exception:
            # The broker may have accepted the order before transport failed.
            # NEVER retry automatically; quarantine pending manual reconciliation.
            try:
                bridge.transition_claimed(cmd, "submitting", "uncertain",
                                          message="Broker submission uncertain; reconcile by client_order_id")
            except Exception:
                logging.exception("Unable to persist uncertain command: %s", cmd["id"])
            raise
        # If acknowledgement persistence fails, state remains 'submitting'.
        # Future claims are blocked pending broker reconciliation.
        bridge.transition_claimed(cmd, "submitting", "acknowledged",
                                  message="Broker accepted, fill not verified",
                                  broker_id=str(accepted.id))
        return accepted

    def execute_loop(self):
        logging.info("E1.3 execution worker started (automated entries disabled)")
        while True:
            try:
                # Disabled until migration, reconciliation tests, and release approval.
                if os.getenv("E1_EXECUTION_ENABLED") != "true":
                    time.sleep(60)
                    continue
                if not DASHBOARD_SYNC or dash is None or not dash.bridge.client:
                    raise RuntimeError("Dashboard bridge required; fail closed")
                bridge = dash.bridge
                if bridge.unresolved_trade_commands():
                    logging.critical("Unresolved trade commands: submissions halted")
                    time.sleep(60)
                    continue
                state = self.sync_portfolio_state()
                if state["equity"] <= 0:
                    raise RuntimeError("Invalid account equity")
                risk_engine = RiskEngine(account_equity=state["equity"], config=self.config)
                cmd = bridge.claim_next_trade_command()
                if cmd is not None:
                    self.process_claimed_order(cmd, bridge, state, risk_engine)
                # Automated strategy orders intentionally disabled in E1.3.1.
            except Exception:
                logging.exception("E1 execution halted for this cycle; no automatic retry")
            time.sleep(60)

if __name__ == "__main__":
    engine = LiveExecutionEngine()
    engine.execute_loop()