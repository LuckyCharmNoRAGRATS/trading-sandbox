"""E1.7 independent, pure order-to-portfolio reconciliation audit.

No command clearance, no broker/database I/O. An exact pre-order baseline and
exclusive trading activity are mandatory for any positive position match.
"""
from decimal import Decimal, InvalidOperation

from order_lifecycle import LifecycleError, normalize_order, compare_snapshots


class ReconciliationError(RuntimeError):
    pass


def _signed_qty(value):
    if value is None or isinstance(value, bool):
        raise ReconciliationError("Missing position quantity")
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ReconciliationError("Invalid position quantity") from exc
    if not qty.is_finite():
        raise ReconciliationError("Nonfinite position quantity")
    return qty


def _attribute(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def audit_order_position(
    order,
    expected_client_id,
    *,
    baseline_quantity,
    broker_positions,
    broker_open_orders,
    exclusive_symbol_activity=False,
    previous_order=None,
):
    """Return a blocked audit report, never permission to trade.

    All broker positions/open orders must be complete and contemporaneous.
    baseline_quantity must be a durable snapshot taken before submission.
    Absence of exclusive activity cannot establish fill-to-position causality.
    """
    ledger = normalize_order(order, expected_client_id)
    if previous_order is not None:
        compare_snapshots(previous_order, ledger)
    if not isinstance(broker_positions, (list, tuple)):
        raise ReconciliationError("Incomplete broker positions")
    if not isinstance(broker_open_orders, (list, tuple)):
        raise ReconciliationError("Incomplete broker open orders")
    baseline = _signed_qty(baseline_quantity)
    symbols = {}
    for position in broker_positions:
        symbol = _attribute(position, "symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ReconciliationError("Invalid broker position")
        if symbol in symbols:
            raise ReconciliationError("Duplicate broker position")
        symbols[symbol] = _signed_qty(_attribute(position, "qty"))
    for pending in broker_open_orders:
        symbol = _attribute(pending, "symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ReconciliationError("Invalid broker pending order")
    observed = symbols.get(ledger.symbol, Decimal("0"))
    sign = Decimal("1") if ledger.side == "buy" else Decimal("-1")
    expected = baseline + sign * ledger.filled_quantity
    matching = observed == expected
    # For filled/cancelled orders, an independently fetched open-order list
    # must not still show this order. Any unresolved open order blocks release.
    pending_count = len(broker_open_orders)
    consistent = matching and exclusive_symbol_activity is True
    return {
        "client_order_id": ledger.client_order_id,
        "broker_order_id": ledger.broker_order_id,
        "status": ledger.status,
        "baseline_quantity": str(baseline),
        "filled_quantity": str(ledger.filled_quantity),
        "expected_position_quantity": str(expected),
        "observed_position_quantity": str(observed),
        "position_matches": matching,
        "exclusive_activity_proven": exclusive_symbol_activity is True,
        "open_order_count": pending_count,
        "lifecycle_terminal": ledger.is_terminal,
        "consistent_under_assumptions": consistent and (
            pending_count == 0 or not ledger.is_terminal
        ),
        "command_clearance_authorized": False,
        "risk_increasing_orders_allowed": False,
        "review_required": True,
    }
