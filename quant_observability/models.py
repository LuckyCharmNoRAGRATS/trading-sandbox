"""Versioned, side-effect-free observation contracts (no broker or database imports)."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re

SCHEMA_VERSION = 1
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def token(value: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError("identifier must be 1-128 safe characters")
    return value


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must include UTC offset")
    return value


def amount(value, *, nonnegative=False, positive=False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("money/quantity must use Decimal or decimal string, never float")
    try:
        result = Decimal(value)
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite() or (nonnegative and result < 0) or (positive and result <= 0):
        raise ValueError("invalid amount")
    return result


@dataclass(frozen=True)
class Meta:
    event_id: str
    observed_at: datetime
    source: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        token(self.event_id)
        token(self.source)
        utc(self.observed_at)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")


@dataclass(frozen=True)
class OrderEvent:
    meta: Meta
    order_id: str
    symbol: str
    side: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal

    def __post_init__(self):
        token(self.order_id); token(self.symbol)
        if self.side not in {"buy", "sell"} or self.status not in {"new", "accepted", "partially_filled", "filled", "canceled", "rejected", "expired"}:
            raise ValueError("invalid order side/status")
        q = amount(self.quantity, positive=True)
        f = amount(self.filled_quantity, nonnegative=True)
        object.__setattr__(self, "quantity", q)
        object.__setattr__(self, "filled_quantity", f)
        if f > q:
            raise ValueError("filled quantity exceeds order quantity")


@dataclass(frozen=True)
class FillEvent:
    meta: Meta
    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    currency: str = "USD"

    def __post_init__(self):
        for val in (self.fill_id, self.order_id, self.symbol, self.currency): token(val)
        if self.side not in {"buy", "sell"}: raise ValueError("invalid side")
        object.__setattr__(self, "quantity", amount(self.quantity, positive=True))
        object.__setattr__(self, "price", amount(self.price, positive=True))
        object.__setattr__(self, "fee", amount(self.fee, nonnegative=True))


@dataclass(frozen=True)
class PositionSnapshot:
    meta: Meta
    symbol: str
    quantity: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal | None
    currency: str = "USD"

    def __post_init__(self):
        token(self.symbol); token(self.currency)
        object.__setattr__(self, "quantity", amount(self.quantity))
        object.__setattr__(self, "market_value", amount(self.market_value))
        if self.unrealized_pnl is not None:
            object.__setattr__(self, "unrealized_pnl", amount(self.unrealized_pnl))


@dataclass(frozen=True)
class CashMovement:
    meta: Meta
    movement_id: str
    kind: str
    value: Decimal
    currency: str = "USD"

    def __post_init__(self):
        token(self.movement_id); token(self.currency)
        if self.kind not in {"deposit", "withdrawal", "dividend", "interest", "trade_settlement", "adjustment"}:
            raise ValueError("invalid cash movement kind")
        object.__setattr__(self, "value", amount(self.value))


@dataclass(frozen=True)
class FeeEvent:
    meta: Meta
    fee_id: str
    kind: str
    value: Decimal
    currency: str = "USD"

    def __post_init__(self):
        token(self.fee_id); token(self.kind); token(self.currency)
        object.__setattr__(self, "value", amount(self.value, nonnegative=True))


@dataclass(frozen=True)
class AccountSnapshot:
    meta: Meta
    equity: Decimal
    cash: Decimal
    realized_pnl_before_fees: Decimal | None = None
    currency: str = "USD"

    def __post_init__(self):
        token(self.currency)
        object.__setattr__(self, "equity", amount(self.equity, nonnegative=True))
        object.__setattr__(self, "cash", amount(self.cash))
        if self.realized_pnl_before_fees is not None:
            object.__setattr__(self, "realized_pnl_before_fees", amount(self.realized_pnl_before_fees))


@dataclass(frozen=True)
class SystemHealth:
    meta: Meta
    component: str
    status: str
    code: str  # fixed machine code, never exception text or credentials

    def __post_init__(self):
        token(self.component); token(self.code)
        if self.status not in {"ok", "degraded", "down", "unknown"}: raise ValueError("invalid health state")


@dataclass(frozen=True)
class DataFreshness:
    meta: Meta
    feed: str
    last_data_at: datetime | None
    threshold_seconds: int

    def __post_init__(self):
        token(self.feed)
        if self.last_data_at is not None: utc(self.last_data_at)
        if not isinstance(self.threshold_seconds, int) or self.threshold_seconds <= 0:
            raise ValueError("invalid staleness threshold")


@dataclass(frozen=True)
class PnLSnapshot:
    meta: Meta
    realized_before_fees: Decimal | None
    unrealized: Decimal | None
    observed_fees: Decimal | None
    currency: str = "USD"

    def __post_init__(self):
        token(self.currency)
        for name in ("realized_before_fees", "unrealized", "observed_fees"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, amount(value, nonnegative=(name == "observed_fees")))


@dataclass(frozen=True)
class DrawdownSnapshot:
    meta: Meta
    peak_equity: Decimal
    current_equity: Decimal
    currency: str = "USD"

    def __post_init__(self):
        token(self.currency)
        object.__setattr__(self, "peak_equity", amount(self.peak_equity, positive=True))
        object.__setattr__(self, "current_equity", amount(self.current_equity, nonnegative=True))

    @property
    def raw_drawdown_pct(self) -> Decimal:
        return max(Decimal(0), (self.peak_equity - self.current_equity) / self.peak_equity * Decimal(100))
