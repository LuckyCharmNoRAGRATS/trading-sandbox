"""Immutable, broker-independent contracts for the risk-only prototype."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple


def to_decimal(value: object) -> Decimal:
    """Allow Python numeric values only; reject booleans, NaN and infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("expected finite numeric value")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ValueError("expected finite numeric value") from exc
    if not number.is_finite():
        raise ValueError("expected finite numeric value")
    return number


@dataclass(frozen=True)
class RiskLimits:
    trade_risk_fraction: Decimal = Decimal("0.01")
    symbol_notional_fraction: Decimal = Decimal("0.05")
    portfolio_gross_fraction: Decimal = Decimal("0.50")
    daily_drawdown_fraction: Decimal = Decimal("0.03")
    max_quote_age_seconds: int = 90
    max_snapshot_age_seconds: int = 90

    def __post_init__(self) -> None:
        for name in ("trade_risk_fraction", "symbol_notional_fraction",
                     "portfolio_gross_fraction", "daily_drawdown_fraction"):
            number = to_decimal(getattr(self, name))
            if not Decimal("0") < number <= Decimal("1"):
                raise ValueError(f"invalid limit: {name}")
            object.__setattr__(self, name, number)
        for name in ("max_quote_age_seconds", "max_snapshot_age_seconds"):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                raise ValueError(f"invalid limit: {name}")


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    symbol: str
    side: str  # BUY or SELL, for a new long or a new short position
    entry_price: Decimal
    stop_price: Decimal
    quote_at: datetime
    requested_quantity: Optional[int] = None  # None => calculate maximum safe integer shares


@dataclass(frozen=True)
class PortfolioSnapshot:
    equity: Decimal
    session_start_equity: Decimal
    gross_exposure: Decimal  # sum of absolute holdings notionals, in equity currency
    pending_exposure: Decimal  # absolute notional of all unfilled broker orders
    positions: Tuple[Tuple[str, Decimal], ...]  # (symbol, absolute notional)
    pending_symbols: Tuple[str, ...]
    as_of: datetime
    session_id: str  # trusted calendar's stable trading-session identifier


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason_code: str
    intent_id: str
    quantity: int = 0
    notional: Decimal = Decimal("0")
    projected_gross_exposure: Decimal = Decimal("0")


@dataclass(frozen=True)
class KillSwitchState:
    manual_active: bool
    daily_latched_session: Optional[str]
    active_session: Optional[str]
    reserved_intents: int
    seen_intents: int
