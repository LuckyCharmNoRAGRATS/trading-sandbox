"""Deterministic, broker-free order matching and simulation-only cash ledger.

All mutations are represented as hash-linked replayable events. The hash chain
is an error-detection aid, NOT a signed audit trail or broker reconciliation.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .models import CostModel, OrderIntent, Quote, SimulationError, number, utc


GENESIS = "0" * 64
ACTIVE = frozenset(("ACCEPTED", "PARTIALLY_FILLED"))
TERMINAL = frozenset(("FILLED", "CANCELED", "REJECTED"))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(payload):
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class ExecutionSimulator:
    """Simulation-only: no broker, live order, network, or risk authorization API."""

    def __init__(self, initial_cash, costs=None):
        self.initial_cash = number(initial_cash, "initial_cash")
        self.costs = costs if costs is not None else CostModel()
        if not isinstance(self.costs, CostModel):
            raise SimulationError("Invalid cost model")
        self.cash = self.initial_cash
        self.positions = {}
        self.total_commission = Decimal("0")
        self.orders = {}  # insertion order gives deterministic price-time priority
        self._keys = {}
        self._events = []
        self._last_quote = None
        self._bid_remaining = Decimal("0")
        self._ask_remaining = Decimal("0")

    def _emit(self, kind, order_id, timestamp, data):
        event = {
            "seq": len(self._events) + 1,
            "kind": kind,
            "order_id": order_id,
            "timestamp": timestamp,
            "data": data,
            "prev_hash": self._events[-1]["event_hash"] if self._events else GENESIS,
        }
        event["event_hash"] = _digest(event)
        self._apply(event)
        self._events.append(event)

    def submit(self, intent: OrderIntent, *, at: datetime):
        """Accept a validated intent; identical idempotency keys are no-ops."""
        if not isinstance(intent, OrderIntent):
            raise SimulationError("Expected OrderIntent")
        stamp = utc(at)
        data = {
            "client_order_id": intent.client_order_id,
            "idempotency_key": intent.idempotency_key,
            "symbol": intent.symbol,
            "side": intent.side,
            "quantity": str(intent.quantity),
            "order_type": intent.order_type,
            "limit_price": str(intent.limit_price) if intent.limit_price is not None else None,
        }
        old = self._keys.get(intent.idempotency_key)
        if old is not None:
            if self.orders[old]["intent"] != data:
                raise SimulationError("Idempotency key collision with different intent")
            return deepcopy(self.orders[old])
        if intent.client_order_id in self.orders:
            raise SimulationError("Client order ID reused with new key")
        self._emit("ACCEPTED", intent.client_order_id, stamp, data)
        return deepcopy(self.orders[intent.client_order_id])

    def cancel(self, client_order_id, *, at: datetime, reason="user_request"):
        self._terminate("CANCELED", client_order_id, at, reason)

    def reject(self, client_order_id, *, at: datetime, reason):
        self._terminate("REJECTED", client_order_id, at, reason)

    def _terminate(self, kind, order_id, at, reason):
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 256:
            raise SimulationError("A nonempty reason is required")
        self._emit(kind, order_id, utc(at), {"reason": reason})

    def on_quote(self, quote: Quote):
        """Apply one synthetic quote; the quote depth is shared among orders."""
        if not isinstance(quote, Quote):
            raise SimulationError("Expected Quote")
        stamp = utc(quote.timestamp)
        self._emit("QUOTE", None, stamp, {
            "bid": str(quote.bid), "ask": str(quote.ask),
            "bid_size": str(quote.bid_size), "ask_size": str(quote.ask_size),
        })
        filled = 0
        for order_id, record in tuple(self.orders.items()):
            if record["status"] not in ACTIVE:
                continue
            if datetime.fromisoformat(stamp) < (
                datetime.fromisoformat(record["accepted_at"]) + timedelta(milliseconds=self.costs.latency_ms)
            ):
                continue
            side = record["intent"]["side"]
            available = self._ask_remaining if side == "buy" else self._bid_remaining
            qty = min(record["quantity"] - record["filled_quantity"], available)
            if qty <= 0:
                continue
            price = self._execution_price(side)
            limit = record["intent"]["limit_price"]
            if limit is not None and ((side == "buy" and price > Decimal(limit)) or
                                      (side == "sell" and price < Decimal(limit))):
                continue
            self._emit("FILL", order_id, stamp, {
                "quantity": str(qty), "price": str(price),
                "commission": str(qty * self.costs.commission_per_share),
            })
            filled += 1
        return filled

    def _execution_price(self, side):
        quote = self._last_quote
        if quote is None:
            raise SimulationError("No quote")
        rate = self.costs.slippage_bps / Decimal("10000")
        return quote.ask * (1 + rate) if side == "buy" else quote.bid * (1 - rate)

    def _apply(self, event):
        kind, order_id, stamp, data = (event[k] for k in ("kind", "order_id", "timestamp", "data"))
        try:
            at = datetime.fromisoformat(stamp)
            utc(at)
        except (ValueError, TypeError) as exc:
            raise SimulationError("Invalid event timestamp") from exc
        if not isinstance(data, dict):
            raise SimulationError("Invalid event data")
        if kind == "QUOTE":
            q = Quote(at, **data)
            if self._last_quote is not None and at <= self._last_quote.timestamp:
                raise SimulationError("Quote timestamps must strictly increase")
            self._last_quote = q
            self._bid_remaining, self._ask_remaining = q.bid_size, q.ask_size
            return
        if kind == "ACCEPTED":
            intent = OrderIntent(**data)
            if order_id != intent.client_order_id or order_id in self.orders or intent.idempotency_key in self._keys:
                raise SimulationError("Duplicate order identity in replay")
            self._keys[intent.idempotency_key] = order_id
            self.orders[order_id] = {
                "intent": data.copy(), "accepted_at": stamp, "updated_at": stamp, "status": "ACCEPTED",
                "quantity": intent.quantity, "filled_quantity": Decimal("0"),
                "commission": Decimal("0"),
            }
            return
        if order_id not in self.orders:
            raise SimulationError("Unknown order identity")
        order = self.orders[order_id]
        if at < datetime.fromisoformat(order["updated_at"]):
            raise SimulationError("Order event timestamp moved backwards")
        if kind in ("CANCELED", "REJECTED"):
            if order["status"] not in (("ACCEPTED",) if kind == "REJECTED" else ACTIVE):
                raise SimulationError("Invalid terminal transition")
            if not isinstance(data.get("reason"), str) or not data["reason"].strip():
                raise SimulationError("Missing termination reason")
            order["status"] = kind
            order["updated_at"] = stamp
            return
        if kind != "FILL":
            raise SimulationError("Unknown event kind")
        if order["status"] not in ACTIVE or self._last_quote is None:
            raise SimulationError("Fill without active order and quote")
        if stamp != utc(self._last_quote.timestamp):
            raise SimulationError("Fill timestamp differs from latest quote")
        accepted = datetime.fromisoformat(order["accepted_at"])
        if at < accepted + timedelta(milliseconds=self.costs.latency_ms):
            raise SimulationError("Fill violates simulated latency")
        qty = number(data.get("quantity"), "fill quantity", positive=True)
        price = number(data.get("price"), "fill price", positive=True)
        fee = number(data.get("commission"), "commission")
        side = order["intent"]["side"]
        if qty > (self._ask_remaining if side == "buy" else self._bid_remaining):
            raise SimulationError("Fill exceeds quoted depth")
        if qty + order["filled_quantity"] > order["quantity"]:
            raise SimulationError("Overfill")
        if price != self._execution_price(side) or fee != qty * self.costs.commission_per_share:
            raise SimulationError("Fill violates deterministic pricing model")
        limit = order["intent"]["limit_price"]
        if limit is not None and ((side == "buy" and price > Decimal(limit)) or
                                  (side == "sell" and price < Decimal(limit))):
            raise SimulationError("Fill breaches limit")
        if side == "buy":
            self._ask_remaining -= qty
            self.cash -= qty * price + fee
            self.positions[order["intent"]["symbol"]] = self.positions.get(order["intent"]["symbol"], Decimal("0")) + qty
        else:
            self._bid_remaining -= qty
            self.cash += qty * price - fee
            self.positions[order["intent"]["symbol"]] = self.positions.get(order["intent"]["symbol"], Decimal("0")) - qty
        order["filled_quantity"] += qty
        order["commission"] += fee
        order["updated_at"] = stamp
        order["status"] = "FILLED" if order["filled_quantity"] == order["quantity"] else "PARTIALLY_FILLED"
        self.total_commission += fee

    def events(self):
        return deepcopy(self._events)

    def ledger(self):
        return {
            "cash": str(self.cash),
            "positions": {k: str(v) for k, v in sorted(self.positions.items()) if v},
            "total_commission": str(self.total_commission),
            "orders": {k: {"status": v["status"], "quantity": str(v["quantity"]),
                            "filled_quantity": str(v["filled_quantity"]),
                            "commission": str(v["commission"])} for k, v in self.orders.items()},
        }

    @classmethod
    def replay(cls, initial_cash, costs, events):
        instance = cls(initial_cash, costs)
        for event in events:
            if not isinstance(event, dict) or set(event) != {
                "seq", "kind", "order_id", "timestamp", "data", "prev_hash", "event_hash"
            }:
                raise SimulationError("Malformed event")
            payload = {key: value for key, value in event.items() if key != "event_hash"}
            if (event["seq"] != len(instance._events) + 1 or
                    event["prev_hash"] != (instance._events[-1]["event_hash"] if instance._events else GENESIS) or
                    event["event_hash"] != _digest(payload)):
                raise SimulationError("Broken sequence or event hash")
            instance._apply(event)
            instance._events.append(deepcopy(event))
        return instance

    def reconcile(self):
        """Replay the simulated ledger; never compare to broker/account state."""
        rebuilt = self.replay(self.initial_cash, self.costs, self.events())
        if rebuilt.ledger() != self.ledger():
            raise SimulationError("Simulation ledger disagrees with event replay")
        return rebuilt.ledger()

    def save_journal(self, path):
        """Atomic single-process snapshot only; not an auto-persisting order outbox."""
        destination = Path(path)
        if not destination.parent.is_dir() or destination.is_symlink():
            raise SimulationError("Unsafe journal destination")
        payload = {
            "schema": 1,
            "initial_cash": str(self.initial_cash),
            "costs": {"slippage_bps": str(self.costs.slippage_bps),
                      "commission_per_share": str(self.costs.commission_per_share),
                      "latency_ms": self.costs.latency_ms},
            "events": self.events(),
        }
        fd, name = tempfile.mkstemp(dir=destination.parent, prefix=".sim-journal-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(_canonical(payload) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, destination)
            if hasattr(os, "O_DIRECTORY"):
                dir_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @classmethod
    def load_journal(cls, path):
        source = Path(path)
        if not source.is_file() or source.is_symlink():
            raise SimulationError("Journal missing or unsafe")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if set(payload) != {"schema", "initial_cash", "costs", "events"} or payload["schema"] != 1:
                raise SimulationError("Unsupported journal schema")
            return cls.replay(payload["initial_cash"], CostModel(**payload["costs"]), payload["events"])
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            raise SimulationError("Corrupt simulator journal") from exc
