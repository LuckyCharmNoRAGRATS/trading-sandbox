"""E1.13 conservative broker snapshot collection.

This module only defines methods that use an injected broker client. Nothing
contacts Alpaca at import time. These checks DO NOT provide atomic snapshots,
pagination across the 500-order API limit, or permission to trade.
"""
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from portfolio_reconciliation import validate_broker_snapshot


class BrokerSnapshotError(RuntimeError):
    pass


MAX_OPEN_ORDERS_PER_PAGE = 500


def _order_value(order, name):
    value = getattr(order, name, None)
    return getattr(value, "value", value)


def _snapshot_signature(account, positions, orders):
    """Exact comparison of two sequential reads; reject drift, not infer atomicity."""
    accounts = tuple(str(getattr(account, key, None)) for key in
                     ("equity", "last_equity", "buying_power"))
    holdings = tuple(sorted((
        str(getattr(p, "symbol", None)),
        str(getattr(p, "qty", None)),
        str(getattr(p, "market_value", None)),
    ) for p in positions))
    pending = tuple(sorted((
        str(_order_value(o, "id")),
        str(_order_value(o, "client_order_id")),
        str(_order_value(o, "symbol")),
        str(_order_value(o, "status")),
        str(_order_value(o, "side")),
        str(_order_value(o, "qty")),
        str(_order_value(o, "filled_qty")),
    ) for o in orders))
    return accounts, holdings, pending


def _read_complete_open_orders(client):
    """One explicit MAX-sized page; a full page makes completeness unknowable.

    Alpaca-py GetOrdersRequest (pinned project release) has timestamp filters
    but no ID cursor. Timestamp-based pagination can omit equal-timestamp
    orders, so at a full page we block rather than silently drop orders.
    """
    request = GetOrdersRequest(
        status=QueryOrderStatus.OPEN, limit=MAX_OPEN_ORDERS_PER_PAGE,
        nested=False,
    )
    orders = client.get_orders(filter=request)
    if not isinstance(orders, list):
        raise BrokerSnapshotError("Broker open orders are not a complete list")
    if len(orders) >= MAX_OPEN_ORDERS_PER_PAGE:
        raise BrokerSnapshotError("Open-order page at API limit: completeness unproven")
    seen = set()
    for order in orders:
        oid = _order_value(order, "id")
        symbol = _order_value(order, "symbol")
        state = _order_value(order, "status")
        if not oid or str(oid) in seen or not isinstance(symbol, str) or not symbol:
            raise BrokerSnapshotError("Missing or duplicate open-order identity")
        if state in (None, "", "filled", "canceled", "expired", "rejected"):
            raise BrokerSnapshotError("Unexpected terminal or unknown open-order status")
        if getattr(order, "legs", None):
            raise BrokerSnapshotError("Nested/multi-leg orders require separate audit")
        seen.add(str(oid))
    return orders


def _read_once(client):
    account = client.get_account()
    positions = client.get_all_positions()
    orders = _read_complete_open_orders(client)
    if account is None or not isinstance(positions, (list, tuple)):
        raise BrokerSnapshotError("Incomplete broker account or positions")
    return account, positions, orders


def collect_stable_portfolio_snapshot(client, *, max_exposure_pct=0.5):
    """Two reads must match; still vulnerable to changes between last read and order.

    No new risk-increasing order should be enabled based on this check alone.
    """
    first = _read_once(client)
    # Freeze the first observation before the second API read: SDK clients
    # may return and mutate references to the same objects between calls.
    first_signature = _snapshot_signature(*first)
    second = _read_once(client)
    if first_signature != _snapshot_signature(*second):
        raise BrokerSnapshotError("Broker account, positions or orders changed between reads")
    state = validate_broker_snapshot(*second, max_exposure_pct=max_exposure_pct)
    state["order_coverage_checked"] = True
    state["observationally_stable"] = True
    return state
