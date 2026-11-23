"""Deterministic, side-effect-free new-entry risk assessment.

This is NOT execution authorization: a broker/OMS adapter must revalidate
and atomically reserve intent IDs before sending any orders.
"""
import re
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from typing import Iterable

from .models import OrderIntent, PortfolioSnapshot, RiskDecision, RiskLimits, to_decimal


_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,15}\Z")
_ZERO = Decimal("0")


def reject(code: str, intent_id: str = "") -> RiskDecision:
    return RiskDecision(False, code, intent_id)


def _valid_instant(instant: object, now: datetime, age_seconds: int) -> bool:
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        return False
    if instant.utcoffset() is None:
        return False
    age = (now - instant).total_seconds()
    return 0 <= age <= age_seconds


def validate_snapshot(snapshot: PortfolioSnapshot, now: datetime, limits: RiskLimits) -> str:
    """Return an error code or the empty string; never trust partial broker state."""
    try:
        if not isinstance(snapshot, PortfolioSnapshot):
            return "INVALID_SNAPSHOT"
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            return "INVALID_CLOCK"
        if not _valid_instant(snapshot.as_of, now, limits.max_snapshot_age_seconds):
            return "STALE_SNAPSHOT"
        if not isinstance(snapshot.session_id, str) or not snapshot.session_id.strip() or len(snapshot.session_id) > 64:
            return "INVALID_SNAPSHOT"
        equity = to_decimal(snapshot.equity)
        start = to_decimal(snapshot.session_start_equity)
        gross = to_decimal(snapshot.gross_exposure)
        pending = to_decimal(snapshot.pending_exposure)
        if equity <= 0 or start <= 0 or gross < 0 or pending < 0:
            return "INVALID_SNAPSHOT"
        if not isinstance(snapshot.positions, tuple) or not isinstance(snapshot.pending_symbols, tuple):
            return "INVALID_SNAPSHOT"
        symbols = set()
        exposure_sum = _ZERO
        for position in snapshot.positions:
            if not isinstance(position, tuple) or len(position) != 2:
                return "INVALID_SNAPSHOT"
            symbol, exposure = position
            if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol) or symbol in symbols:
                return "INVALID_SNAPSHOT"
            position_notional = to_decimal(exposure)
            if position_notional <= 0:
                return "INVALID_SNAPSHOT"
            exposure_sum += position_notional
            symbols.add(symbol)
        if exposure_sum > gross:
            return "INVALID_SNAPSHOT"
        if any(not isinstance(s, str) or not _SYMBOL.fullmatch(s)
               for s in snapshot.pending_symbols):
            return "INVALID_SNAPSHOT"
        if bool(snapshot.pending_symbols) != bool(pending):
            return "INVALID_SNAPSHOT"
        return ""
    except (TypeError, ValueError, ArithmeticError):
        return "INVALID_SNAPSHOT"


def assess_order(
    intent: OrderIntent,
    snapshot: PortfolioSnapshot,
    limits: RiskLimits,
    now: datetime,
    *,
    reserved_notional: Decimal = _ZERO,
    reserved_symbols: Iterable[str] = (),
) -> RiskDecision:
    """Conservative whole-share sizing; rejection has quantity zero.

    snapshot.pending_exposure is already reserved with the broker; reserved_notional
    represents additional local approvals not yet reflected in that snapshot.
    """
    ident = intent.intent_id if isinstance(intent, OrderIntent) and isinstance(intent.intent_id, str) else ""
    try:
        if not isinstance(limits, RiskLimits):
            return reject("INVALID_POLICY", ident)
        issue = validate_snapshot(snapshot, now, limits)
        if issue:
            return reject(issue, ident)
        if not isinstance(intent, OrderIntent):
            return reject("INVALID_INTENT", ident)
        if not isinstance(intent.intent_id, str) or not intent.intent_id.strip() or len(intent.intent_id) > 128:
            return reject("INVALID_INTENT", ident)
        if not isinstance(intent.symbol, str) or not _SYMBOL.fullmatch(intent.symbol):
            return reject("INVALID_INTENT", ident)
        if intent.side not in ("BUY", "SELL"):
            return reject("INVALID_INTENT", ident)
        if not _valid_instant(intent.quote_at, now, limits.max_quote_age_seconds):
            return reject("STALE_QUOTE", ident)
        entry = to_decimal(intent.entry_price)
        stop = to_decimal(intent.stop_price)
        reserved = to_decimal(reserved_notional)
        if entry <= 0 or stop <= 0 or reserved < 0:
            return reject("INVALID_INTENT", ident)
        if (intent.side == "BUY" and stop >= entry) or (intent.side == "SELL" and stop <= entry):
            return reject("INVALID_STOP", ident)
        if intent.requested_quantity is not None and (
            isinstance(intent.requested_quantity, bool)
            or not isinstance(intent.requested_quantity, int)
            or intent.requested_quantity <= 0
        ):
            return reject("INVALID_INTENT", ident)
        if intent.symbol in {s for s, _ in snapshot.positions} or intent.symbol in snapshot.pending_symbols:
            return reject("SYMBOL_ALREADY_ACTIVE", ident)
        if intent.symbol in set(reserved_symbols):
            return reject("SYMBOL_RESERVED", ident)

        equity = to_decimal(snapshot.equity)
        start = to_decimal(snapshot.session_start_equity)
        daily_dd = (start - equity) / start
        if daily_dd >= limits.daily_drawdown_fraction:
            return reject("DAILY_DRAWDOWN", ident)

        occupied = to_decimal(snapshot.gross_exposure) + to_decimal(snapshot.pending_exposure) + reserved
        max_gross = equity * limits.portfolio_gross_fraction
        if occupied >= max_gross:
            return reject("PORTFOLIO_LIMIT", ident)

        risk_per_share = abs(entry - stop)
        budget_risk = equity * limits.trade_risk_fraction
        budget_symbol = equity * limits.symbol_notional_fraction
        budget_gross = max_gross - occupied
        risk_qty = int((budget_risk / risk_per_share).to_integral_value(rounding=ROUND_FLOOR))
        symbol_qty = int((budget_symbol / entry).to_integral_value(rounding=ROUND_FLOOR))
        gross_qty = int((budget_gross / entry).to_integral_value(rounding=ROUND_FLOOR))
        ceiling = min(risk_qty, symbol_qty, gross_qty)
        if ceiling <= 0:
            return reject("ZERO_SIZE", ident)
        if intent.requested_quantity is not None and intent.requested_quantity > ceiling:
            return reject("LIMIT_BREACHED", ident)
        qty = ceiling if intent.requested_quantity is None else intent.requested_quantity
        notional = entry * qty
        return RiskDecision(True, "APPROVED", ident, qty, notional, occupied + notional)
    except (TypeError, ValueError, ArithmeticError, OverflowError):
        return reject("INVALID_INPUT", ident)
