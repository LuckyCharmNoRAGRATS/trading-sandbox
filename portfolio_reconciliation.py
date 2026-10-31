"""E1.5: pure, fail-closed broker portfolio snapshot validation.

Does not fetch data, place orders, mutate broker state, or clear command blocks.
Open orders remain a separate hard blocker until reservations/reconciliation exist.
"""
import math
import re


class PortfolioStateError(RuntimeError):
    pass


def _amount(value, name, *, allow_zero=True):
    try:
        if value is None or isinstance(value, bool):
            raise ValueError(name)
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise PortfolioStateError(f"Invalid {name}") from exc
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        raise PortfolioStateError(f"Invalid {name}")
    return number


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", value):
        raise PortfolioStateError("Invalid broker symbol")
    return value


def validate_broker_snapshot(account, positions, orders, max_exposure_pct=0.5):
    """Validate a complete snapshot; does not establish cross-call atomicity.

    Order list MUST contain all open orders, not only DAY orders or one page.
    Portfolio exposure is absolute position market value (shorts count positively).
    """
    if account is None or positions is None or orders is None:
        raise PortfolioStateError("Incomplete broker snapshot")
    if not isinstance(positions, (list, tuple)) or not isinstance(orders, (list, tuple)):
        raise PortfolioStateError("Incomplete broker collection")
    equity = _amount(getattr(account, "equity", None), "equity", allow_zero=False)
    previous_equity = _amount(getattr(account, "last_equity", None), "start of day equity", allow_zero=False)
    buying_power = _amount(getattr(account, "buying_power", None), "buying power")
    limit = _amount(max_exposure_pct, "exposure limit", allow_zero=False)
    if limit > 1:
        raise PortfolioStateError("Exposure limit exceeds 100%")
    seen = set()
    exposure = 0.0
    for position in positions:
        symbol = _symbol(getattr(position, "symbol", None))
        if symbol in seen:
            raise PortfolioStateError("Duplicate broker position symbol")
        seen.add(symbol)
        qty = getattr(position, "qty", None)
        try:
            if qty is None or isinstance(qty, bool):
                raise ValueError("quantity")
            quantity = float(qty)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PortfolioStateError("Invalid position quantity") from exc
        if not math.isfinite(quantity) or quantity == 0:
            raise PortfolioStateError("Invalid position quantity")
        raw_value = getattr(position, "market_value", None)
        try:
            if raw_value is None or isinstance(raw_value, bool):
                raise ValueError("market value")
            market_value = float(raw_value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PortfolioStateError("Invalid position market value") from exc
        if not math.isfinite(market_value) or market_value == 0:
            raise PortfolioStateError("Invalid position market value")
        exposure += abs(market_value)
    pending = []
    for order in orders:
        pending.append(_symbol(getattr(order, "symbol", None)))
    if not math.isfinite(exposure):
        raise PortfolioStateError("Exposure overflow")
    if exposure > equity * limit:
        raise PortfolioStateError("Portfolio exceeds exposure ceiling")
    return {
        "equity": equity,
        "start_of_day_equity": previous_equity,
        "buying_power": buying_power,
        "open_symbols": sorted(seen),
        "pending_symbols": pending,
        "portfolio_exposure": exposure,
        "open_order_count": len(pending),
        "risk_increasing_orders_allowed": len(pending) == 0,
    }


def assert_can_claim_new_trade(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("risk_increasing_orders_allowed") is not True:
        raise PortfolioStateError("Open orders or unverified portfolio snapshot")
    if snapshot.get("open_order_count") != 0 or snapshot.get("pending_symbols") != []:
        raise PortfolioStateError("Pending broker orders must be reconciled first")
    return True
