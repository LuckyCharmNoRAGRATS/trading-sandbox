"""Immutable simulation inputs; no broker imports or network calls."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re


class SimulationError(ValueError):
    """Invalid input, transition, or corrupt event history."""


def number(value, name, *, positive=False):
    if value is None or isinstance(value, bool):
        raise SimulationError(f"Invalid {name}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SimulationError(f"Invalid {name}") from exc
    if not result.is_finite() or result < 0 or (positive and not result):
        raise SimulationError(f"Invalid {name}")
    return result


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SimulationError("Timestamp must be timezone aware")
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class OrderIntent:
    client_order_id: str
    idempotency_key: str
    symbol: str
    side: str
    quantity: Decimal
    order_type: str = "market"
    limit_price: Decimal | None = None

    def __post_init__(self):
        for name in ("client_order_id", "idempotency_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise SimulationError(f"Invalid {name}")
        if not isinstance(self.symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,19}", self.symbol):
            raise SimulationError("Invalid symbol")
        if self.side not in ("buy", "sell"):
            raise SimulationError("Invalid side")
        if self.order_type not in ("market", "limit"):
            raise SimulationError("Invalid order type")
        object.__setattr__(self, "quantity", number(self.quantity, "quantity", positive=True))
        if self.order_type == "limit":
            object.__setattr__(self, "limit_price", number(self.limit_price, "limit_price", positive=True))
        elif self.limit_price is not None:
            raise SimulationError("Market orders cannot have a limit price")


@dataclass(frozen=True)
class Quote:
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal

    def __post_init__(self):
        utc(self.timestamp)
        for field in ("bid", "ask"):
            object.__setattr__(self, field, number(getattr(self, field), field, positive=True))
        for field in ("bid_size", "ask_size"):
            object.__setattr__(self, field, number(getattr(self, field), field))
        if self.ask < self.bid:
            raise SimulationError("Crossed quote is unsupported")


@dataclass(frozen=True)
class CostModel:
    slippage_bps: Decimal = Decimal("0")
    commission_per_share: Decimal = Decimal("0")
    latency_ms: int = 0

    def __post_init__(self):
        for field in ("slippage_bps", "commission_per_share"):
            object.__setattr__(self, field, number(getattr(self, field), field))
        if self.slippage_bps >= 10000:
            raise SimulationError("slippage_bps must be < 10000")
        if not isinstance(self.latency_ms, int) or isinstance(self.latency_ms, bool) or self.latency_ms < 0:
            raise SimulationError("Invalid latency_ms")
