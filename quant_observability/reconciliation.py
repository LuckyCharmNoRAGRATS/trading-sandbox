"""Deterministic comparisons of externally supplied snapshots; never performs I/O."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable
from .models import FillEvent, PositionSnapshot, AccountSnapshot


@dataclass(frozen=True)
class ReconciliationResult:
    verified: bool
    matched_fills: int
    issues: tuple[str, ...]


def _index(items, key, label, issues):
    output = {}
    for item in items:
        ident = key(item)
        if ident in output:
            issues.append(f"duplicate_{label}:{ident}")
        else:
            output[ident] = item
    return output


def reconcile(
    recorded_fills: Iterable[FillEvent], broker_fills: Iterable[FillEvent],
    recorded_positions: Iterable[PositionSnapshot], broker_positions: Iterable[PositionSnapshot],
    recorded_account: AccountSnapshot | None, broker_account: AccountSnapshot | None,
    *, broker_snapshot_complete: bool = False,
    tolerance: Decimal = Decimal("0.00000001"),
) -> ReconciliationResult:
    """Full-record equality for fills; quantity and value checks for positions/accounts.

    Explicit `broker_snapshot_complete=True` means the caller attests complete,
    time-aligned external data. It does NOT initiate a broker connection.
    """
    issues: list[str] = []
    if tolerance < 0 or not tolerance.is_finite(): raise ValueError("invalid tolerance")
    local = _index(recorded_fills, lambda f: f.fill_id, "recorded_fill", issues)
    broker = _index(broker_fills, lambda f: f.fill_id, "broker_fill", issues)
    for fid in sorted(local.keys() - broker.keys()): issues.append(f"missing_broker_fill:{fid}")
    for fid in sorted(broker.keys() - local.keys()): issues.append(f"missing_recorded_fill:{fid}")
    matched = 0
    for fid in sorted(local.keys() & broker.keys()):
        l, b = local[fid], broker[fid]
        comparable = ("order_id", "symbol", "side", "quantity", "price", "fee", "currency")
        if any(getattr(l, x) != getattr(b, x) for x in comparable):
            issues.append(f"fill_mismatch:{fid}")
        else:
            matched += 1
    lp = _index(recorded_positions, lambda p: p.symbol, "recorded_position", issues)
    bp = _index(broker_positions, lambda p: p.symbol, "broker_position", issues)
    for sym in sorted(lp.keys() - bp.keys()): issues.append(f"missing_broker_position:{sym}")
    for sym in sorted(bp.keys() - lp.keys()): issues.append(f"missing_recorded_position:{sym}")
    for sym in sorted(lp.keys() & bp.keys()):
        a, b = lp[sym], bp[sym]
        if a.currency != b.currency or abs(a.quantity-b.quantity) > tolerance or abs(a.market_value-b.market_value) > tolerance or (a.unrealized_pnl is None) != (b.unrealized_pnl is None) or (a.unrealized_pnl is not None and abs(a.unrealized_pnl-b.unrealized_pnl) > tolerance):
            issues.append(f"position_mismatch:{sym}")
    if recorded_account is None or broker_account is None:
        issues.append("account_missing")
    elif recorded_account.currency != broker_account.currency or abs(recorded_account.cash-broker_account.cash) > tolerance or abs(recorded_account.equity-broker_account.equity) > tolerance or recorded_account.realized_pnl_before_fees != broker_account.realized_pnl_before_fees:
        issues.append("account_mismatch")
    if not broker_snapshot_complete:
        issues.append("broker_snapshot_not_confirmed_complete")
    return ReconciliationResult(not issues, matched, tuple(issues))
