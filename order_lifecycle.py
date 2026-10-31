"""E1.6 pure order lifecycle accounting. No broker I/O or order submission.

A fill event is cumulative broker state, not a delta. Never add the same fill
twice across polling/restarts. This module does not authorize command clearance.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class LifecycleError(RuntimeError):
    pass


TERMINAL = frozenset({
    "filled", "canceled", "expired", "rejected", "done_for_day",
})
ACTIVE = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding",
    "partially_filled", "pending_cancel", "pending_replace",
    "stopped", "suspended", "calculated",
})
# Replacement of an order may change identity; quarantine rather than assume
# that it is safe to release funds or reconstruct its remaining quantity.
QUARANTINED = frozenset({"replaced"})
KNOWN = TERMINAL | ACTIVE | QUARANTINED


def _decimal(value, field, *, positive=False):
    if value is None or isinstance(value, bool):
        raise LifecycleError(f"Missing or invalid {field}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LifecycleError(f"Invalid {field}") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise LifecycleError(f"Invalid {field}")
    return number


def _field(order, name):
    if isinstance(order, dict):
        return order.get(name)
    return getattr(order, name, None)


@dataclass(frozen=True)
class OrderLedger:
    client_order_id: str
    broker_order_id: str
    symbol: str
    status: str
    side: str
    quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    filled_avg_price: Decimal | None
    is_terminal: bool
    requires_review: bool

    def as_dict(self):
        return {
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "symbol": self.symbol,
            "status": self.status,
            "side": self.side,
            "quantity": str(self.quantity),
            "filled_quantity": str(self.filled_quantity),
            "remaining_quantity": str(self.remaining_quantity),
            "filled_avg_price": (
                str(self.filled_avg_price) if self.filled_avg_price is not None else None
            ),
            "is_terminal": self.is_terminal,
            "requires_review": self.requires_review,
        }


def normalize_order(order, expected_client_id):
    """Normalize cumulative order status, preserving fractional quantities."""
    if not isinstance(expected_client_id, str) or not expected_client_id:
        raise LifecycleError("Expected client order ID missing")
    client_id = _field(order, "client_order_id")
    broker_id = _field(order, "id")
    if client_id != expected_client_id or not broker_id:
        raise LifecycleError("Broker identity mismatch")
    symbol = _field(order, "symbol")
    if not isinstance(symbol, str) or not symbol:
        raise LifecycleError("Missing symbol")
    raw_side = _field(order, "side")
    side = getattr(raw_side, "value", raw_side)
    if side not in ("buy", "sell"):
        raise LifecycleError("Unknown order side")
    raw_status = _field(order, "status")
    status = getattr(raw_status, "value", raw_status)
    if status not in KNOWN:
        raise LifecycleError("Unknown broker status")
    qty = _decimal(_field(order, "qty"), "order quantity", positive=True)
    filled = _decimal(_field(order, "filled_qty"), "filled quantity")
    if filled > qty:
        raise LifecycleError("Filled quantity exceeds ordered quantity")
    avg_raw = _field(order, "filled_avg_price")
    avg = None if avg_raw is None else _decimal(avg_raw, "fill price", positive=True)
    if filled > 0 and avg is None:
        raise LifecycleError("Filled order lacks average price")
    if filled == 0 and avg is not None:
        raise LifecycleError("Unfilled order reports fill price")
    if status == "filled" and filled != qty:
        raise LifecycleError("Filled state contradicts quantity")
    if status == "partially_filled" and not (0 < filled < qty):
        raise LifecycleError("Partial fill state contradicts quantity")
    if status in {"new", "pending_new", "accepted", "accepted_for_bidding"} and filled != 0:
        raise LifecycleError("Unfilled state contradicts quantity")
    remaining = qty - filled
    # Terminal cancellations may include partial fills. The unfilled remainder
    # is canceled, NOT an automatically safe capital reservation release.
    return OrderLedger(
        client_order_id=client_id,
        broker_order_id=str(broker_id),
        symbol=symbol,
        status=status,
        side=side,
        quantity=qty,
        filled_quantity=filled,
        remaining_quantity=remaining,
        filled_avg_price=avg,
        is_terminal=status in TERMINAL,
        requires_review=True,
    )


def compare_snapshots(previous, current):
    """Check cumulative fill monotonicity for the SAME broker order identity."""
    if previous.client_order_id != current.client_order_id or (
        previous.broker_order_id != current.broker_order_id
    ):
        raise LifecycleError("Broker order identity changed")
    if previous.symbol != current.symbol or previous.side != current.side:
        raise LifecycleError("Order instrument or side changed")
    if previous.quantity != current.quantity:
        raise LifecycleError("Order quantity changed; replacement requires audit")
    if current.filled_quantity < previous.filled_quantity:
        raise LifecycleError("Cumulative filled quantity went backwards")
    if previous.is_terminal and (
        previous.status != current.status
        or previous.filled_quantity != current.filled_quantity
    ):
        raise LifecycleError("Terminal order state changed")
    return current.filled_quantity - previous.filled_quantity


def reserve_open_order_notional(ledger, reference_price):
    """Conservative reference-price reservation for the unfilled remainder.

    This is NOT a safe substitute for broker-side buying power, short margin,
    held orders, or order replacement accounting.
    """
    price = _decimal(reference_price, "reference price", positive=True)
    if ledger.is_terminal:
        return Decimal("0")
    if ledger.status in QUARANTINED:
        raise LifecycleError("Replaced order requires independent reconciliation")
    return ledger.remaining_quantity * price
