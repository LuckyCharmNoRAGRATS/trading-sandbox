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
from portfolio_reconciliation import validate_broker_snapshot, assert_can_claim_new_trade
from reconciliation_audit import audit_order_position
from execution_audit import ExecutionAuditStore, AuditIntegrityError

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
        
        # E1.5: never substitute partial/invalid broker state with zero exposure.
        state = validate_broker_snapshot(
            account, positions, orders,
            max_exposure_pct=self.config.max_portfolio_exposure_pct,
        )
        self.active_positions = {p.symbol: float(p.qty) for p in positions}

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

    def require_audit_store(self):
        """Persistent audit evidence is mandatory even for direct process calls."""
        existing = getattr(self, "audit_store", None)
        if existing is not None:
            existing.verify()
            return existing
        path = os.getenv("E1_AUDIT_DB_PATH")
        if not path:
            raise AuditIntegrityError("E1_AUDIT_DB_PATH required; execution fail closed")
        store = ExecutionAuditStore(path)
        store.verify()
        self.audit_store = store
        return store

    def validate_restart_audit(self, bridge):
        """Audit every unresolved command before further activity, no auto-release."""
        store = self.require_audit_store()
        unresolved = bridge.list_unresolved_trade_commands()
        for command in unresolved:
            store.recover_command(command["id"], command["client_order_id"])
        return {"unresolved_count": len(unresolved), "clearance_authorized": False}

    def process_claimed_order(self, cmd, bridge, state, risk_engine):
        """Process ONE owned command, never retry after a possible broker submission.

        This requires the unapplied E1.3 schema. All broker calls use paper=True.
        """
        from alpaca.trading.requests import MarketOrderRequest
        if cmd.get("cmd") != "submit_order" or cmd.get("status") != "claimed":
            raise ValueError("Unclaimed or unsupported command")
        client_id = cmd.get("client_order_id")
        if (
            not isinstance(cmd.get("id"), int)
            or isinstance(cmd.get("id"), bool)
            or cmd["id"] <= 0
            or not cmd.get("claim_token")
            or client_id != f'e1-{cmd["id"]}'
        ):
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

        store = self.require_audit_store()
        # Provenance: only a validated, complete portfolio snapshot can supply
        # the pre-order baseline. Never infer a missing position from partial data.
        if state.get("snapshot_verified") is not True:
            raise AuditIntegrityError("Unverified portfolio snapshot")
        quantities = state.get("position_quantities")
        if not isinstance(quantities, dict):
            raise AuditIntegrityError("Position quantities missing")
        if symbol in quantities:
            baseline_qty = quantities[symbol]
        elif symbol not in state["open_symbols"]:
            baseline_qty = "0"
        else:
            raise AuditIntegrityError("Open position missing from validated quantities")
        if store.history(cmd["id"]):
            raise AuditIntegrityError("Command already has execution evidence; no resubmission")
        store.append(cmd["id"], client_id, "pre_submit_baseline", {
            "client_order_id": client_id, "symbol": symbol,
            "position_qty": str(baseline_qty),
            "approved_quantity": str(shares), "side": side.value,
        })
        # Database transition and local intent must both be confirmed before broker I/O.
        bridge.transition_claimed(cmd, "claimed", "submitting")
        store.append(cmd["id"], client_id, "submission_intent", {
            "symbol": symbol, "quantity": str(shares), "side": side.value,
        })
        request = MarketOrderRequest(
            symbol=symbol, qty=shares, side=side, time_in_force=TimeInForce.DAY,
            client_order_id=client_id,
        )
        try:
            accepted = self.trading_client.submit_order(request)
        except Exception:
            try:
                store.append(cmd["id"], client_id, "submission_uncertain",
                             {"reason": "broker_call_exception"})
            except Exception:
                logging.exception("Unable to persist broker uncertainty in audit store")
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
        store.append(cmd["id"], client_id, "broker_order_snapshot",
                     {"broker_order_id": str(accepted.id), "status": "accepted_unverified"})
        bridge.transition_claimed(cmd, "submitting", "acknowledged",
                                  message="Broker accepted, fill not verified",
                                  broker_id=str(accepted.id))
        return accepted

    def reconcile_unresolved_commands(self, bridge):
        """Read-only broker lookup; persist identity only, NEVER retry submission.

        Matched orders remain 'acknowledged', which is a blocking state until
        independent order and portfolio reconciliation is implemented.
        """
        commands = bridge.list_unresolved_trade_commands()
        outcomes = []
        for cmd in commands:
            if cmd.get("cmd") != "submit_order":
                raise RuntimeError("Unexpected reconciliation command")
            status = cmd.get("status")
            if status == "claimed":
                # No durable pre-submit transition exists. Human review required.
                outcomes.append((cmd["id"], "claimed_requires_review"))
                continue
            if status not in ("submitting", "uncertain", "acknowledged"):
                raise RuntimeError("Unexpected reconciliation state")
            client_id = cmd.get("client_order_id")
            if (
                not isinstance(cmd.get("id"), int)
                or isinstance(cmd["id"], bool)
                or not cmd.get("claim_token")
                or client_id != f'e1-{cmd["id"]}'
            ):
                raise RuntimeError("Invalid unresolved command identity")
            try:
                order = self.trading_client.get_order_by_client_id(client_id)
            except Exception:
                # Includes not-found, timeout and network failures: no submission.
                logging.exception("Broker lookup unresolved for command %s", cmd["id"])
                outcomes.append((cmd["id"], "lookup_unresolved"))
                continue
            if order is None:
                outcomes.append((cmd["id"], "lookup_unresolved"))
                continue
            broker_client_id = getattr(order, "client_order_id", None)
            broker_id = getattr(order, "id", None)
            raw_status = getattr(order, "status", None)
            order_status = getattr(raw_status, "value", raw_status)
            if (broker_client_id != client_id or broker_id is None
                    or not isinstance(order_status, str) or not order_status):
                raise RuntimeError("Ambiguous or mismatched broker response")
            if cmd.get("broker_order_id") not in (None, "", str(broker_id)):
                raise RuntimeError("Stored broker order ID conflicts with broker")
            # Broker lookup proves an identity, not an order fill or safe exposure.
            # Even terminal broker states do not release the command here.
            if status != "acknowledged":
                bridge.transition_claimed(
                    cmd, status, "acknowledged",
                    message=f"Broker identity verified; state={order_status}; portfolio review pending",
                    broker_id=str(broker_id),
                )
            outcomes.append((cmd["id"], "matched_still_blocked"))
        return outcomes

    def audit_order_against_portfolio(
        self, order, expected_client_id, baseline_quantity, positions,
        open_orders, *, exclusive_symbol_activity=False, previous_order=None,
    ):
        """E1.7 read-only audit; NEVER clears claimed or acknowledged commands.

        Broker snapshots and the pre-submit baseline are supplied by the caller.
        A future separately approved release may persist signed audit records.
        """
        return audit_order_position(
            order, expected_client_id,
            baseline_quantity=baseline_quantity,
            broker_positions=positions,
            broker_open_orders=open_orders,
            exclusive_symbol_activity=exclusive_symbol_activity,
            previous_order=previous_order,
        )

    def record_reconciliation_evidence(
        self, audit_db_path, command_id, client_order_id, *,
        baseline=None, broker_order=None, portfolio_snapshot=None,
        reconciliation_report=None,
    ):
        """E1.8 explicit evidence entrypoint. Does not contact broker/DB or clear orders.

        The baseline is a pre-submission observation supplied by a caller. This
        method cannot retroactively prove when a baseline was actually taken.
        """
        store = ExecutionAuditStore(audit_db_path)
        supplied = [
            ("pre_submit_baseline", baseline),
            ("broker_order_snapshot", broker_order),
            ("portfolio_snapshot", portfolio_snapshot),
            ("reconciliation_report", reconciliation_report),
        ]
        provided = [(kind, record) for kind, record in supplied if record is not None]
        if len(provided) != 1:
            raise AuditIntegrityError("Record exactly one event at a time")
        kind, record = provided[0]
        if kind == "pre_submit_baseline":
            if store.history(command_id):
                raise AuditIntegrityError("Baseline must precede all other command evidence")
        else:
            store.baseline(command_id)
        return store.append(command_id, client_order_id, kind, record)

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
                # Mandatory disk verification, including every unresolved command.
                self.validate_restart_audit(bridge)
                if bridge.unresolved_trade_commands():
                    # Reconciliation is independently opt-in; even a successful
                    # lookup NEVER allows this loop to submit another order.
                    if os.getenv("E1_RECONCILIATION_ENABLED") == "true":
                        self.reconcile_unresolved_commands(bridge)
                    logging.critical("Unresolved trade commands: submissions halted")
                    time.sleep(60)
                    continue
                state = self.sync_portfolio_state()
                if state["equity"] <= 0:
                    raise RuntimeError("Invalid account equity")
                # Open/unreconciled orders reserve risk and block new claims;
                # no automatic portfolio clearance is authorized in E1.5.
                assert_can_claim_new_trade(state)
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