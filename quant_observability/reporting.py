"""No-side-effect reporting, freshness, drawdown, and allowlisted log metadata."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable
from .models import AccountSnapshot, PositionSnapshot, CashMovement, FeeEvent, DataFreshness, Meta, utc
from .reconciliation import ReconciliationResult


@dataclass(frozen=True)
class PortfolioReport:
    as_of: datetime
    currency: str
    status: str
    reconciliation_issues: tuple[str, ...]
    equity: Decimal
    cash: Decimal
    positions_count: int
    unrealized_pnl: Decimal | None
    realized_pnl_before_fees: Decimal | None
    total_observed_fees: Decimal
    net_realized_pnl: Decimal | None
    net_external_cash_flow: Decimal
    raw_equity_drawdown_pct: Decimal | None


def report(account: AccountSnapshot, positions: Iterable[PositionSnapshot],
           fees: Iterable[FeeEvent], cash_movements: Iterable[CashMovement],
           reconciliation: ReconciliationResult,
           *, peak_equity: Decimal | None = None, fees_complete: bool = False) -> PortfolioReport:
    """Amounts are observational; no fill-to-P&L inference or trading action.

    Fees must be COMPLETE for the selected reporting period before treating
    `net_realized_pnl` as period net realized P&L. No cross-period inference.
    """
    positions = tuple(positions); fees = tuple(fees); cash_movements = tuple(cash_movements)
    if any(x.currency != account.currency for x in (*positions, *fees, *cash_movements)):
        raise ValueError("mixed currencies require upstream FX normalization")
    if len({f.fee_id for f in fees}) != len(fees): raise ValueError("duplicate fees")
    if len({f.movement_id for f in cash_movements}) != len(cash_movements): raise ValueError("duplicate movements")
    if len({p.symbol for p in positions}) != len(positions): raise ValueError("duplicate positions")
    unrl = sum((p.unrealized_pnl for p in positions), Decimal(0)) if all(p.unrealized_pnl is not None for p in positions) else None
    total_fees = sum((f.value for f in fees), Decimal(0))
    external = sum((c.value for c in cash_movements if c.kind in {"deposit", "withdrawal"}), Decimal(0))
    dd = None
    if peak_equity is not None:
        if peak_equity <= 0: raise ValueError("peak equity must be positive")
        dd = max(Decimal(0), (peak_equity-account.equity) / peak_equity * Decimal(100))
    # Unknown/incomplete fee ledger cannot establish a net P&L value.
    # Only an explicit complete reconciliation permits net calculation.
    net = account.realized_pnl_before_fees - total_fees if reconciliation.verified and fees_complete and account.realized_pnl_before_fees is not None else None
    return PortfolioReport(account.meta.observed_at, account.currency,
                           "RECONCILED" if reconciliation.verified else "UNVERIFIED",
                           reconciliation.issues, account.equity, account.cash,
                           len(positions), unrl, account.realized_pnl_before_fees,
                           total_fees, net, external, dd)


def freshness(obs: DataFreshness, *, now: datetime) -> str:
    utc(now)
    if obs.last_data_at is None: return "UNKNOWN"
    age = (now - obs.last_data_at).total_seconds()
    if age < 0: return "CLOCK_SKEW"
    return "STALE" if age > obs.threshold_seconds else "FRESH"


def audit_metadata(meta: Meta, kind: str, status: str) -> dict[str, str | int]:
    """Allowlist-only audit metadata: NEVER format arbitrary payloads/exceptions."""
    from .models import token
    from hashlib import sha256
    source = meta.source if meta.source in {"synthetic", "oms", "broker", "risk", "data", "reconciler", "dashboard"} else "other"
    return {"schema_version": meta.schema_version,
            "event_hash": sha256(meta.event_id.encode("utf-8")).hexdigest()[:16],
            "source": source, "observed_at": meta.observed_at.isoformat(),
            "kind": token(kind), "status": token(status)}
