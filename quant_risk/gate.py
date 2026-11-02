"""Process-local, locked preflight gateway; no broker or external I/O.

An OMS must provide a durable atomic reservation/kill-state backend before
this component can be authoritative across multiple processes or restarts.
"""
from datetime import datetime
from decimal import Decimal
from threading import RLock
from typing import Dict, Set, Tuple

from .models import KillSwitchState, OrderIntent, PortfolioSnapshot, RiskDecision, RiskLimits, to_decimal
from .policy import assess_order, reject, validate_snapshot


class RiskGate:
    def __init__(self, limits: RiskLimits = RiskLimits()) -> None:
        if not isinstance(limits, RiskLimits):
            raise ValueError("limits must be RiskLimits")
        self.limits = limits
        self._lock = RLock()
        self._active_session = None
        self._daily_latched_session = None
        self._manual_reason = None
        self._seen_intents: Set[str] = set()
        self._reservations: Dict[str, Tuple[str, Decimal]] = {}

    @property
    def state(self) -> KillSwitchState:
        with self._lock:
            return KillSwitchState(
                manual_active=self._manual_reason is not None,
                daily_latched_session=self._daily_latched_session,
                active_session=self._active_session,
                reserved_intents=len(self._reservations),
                seen_intents=len(self._seen_intents),
            )

    def preflight(
        self, intent: OrderIntent, snapshot: PortfolioSnapshot, now: datetime
    ) -> RiskDecision:
        """Validate-and-reserve atomically within this one Python process."""
        ident = intent.intent_id if isinstance(intent, OrderIntent) and isinstance(intent.intent_id, str) else ""
        with self._lock:
            error = validate_snapshot(snapshot, now, self.limits)
            if error:
                return reject(error, ident)
            if self._manual_reason is not None:
                return reject("MANUAL_KILL", ident)
            if self._daily_latched_session is not None:
                return reject("DAILY_KILL_LATCHED", ident)
            if self._active_session is not None and snapshot.session_id != self._active_session:
                return reject("SESSION_CHANGE_REQUIRES_RESET", ident)

            # Check the daily portfolio breaker before any order-specific validation.
            equity = to_decimal(snapshot.equity)
            start = to_decimal(snapshot.session_start_equity)
            if (start - equity) / start >= self.limits.daily_drawdown_fraction:
                self._daily_latched_session = snapshot.session_id
                self._active_session = snapshot.session_id
                return reject("DAILY_DRAWDOWN", ident)

            if ident in self._seen_intents:
                return reject("DUPLICATE_INTENT", ident)

            reserved = sum((value[1] for value in self._reservations.values()), Decimal("0"))
            decision = assess_order(
                intent, snapshot, self.limits, now,
                reserved_notional=reserved,
                reserved_symbols=(v[0] for v in self._reservations.values()),
            )
            if not decision.allowed:
                return decision
            self._active_session = snapshot.session_id
            self._seen_intents.add(ident)
            self._reservations[ident] = (intent.symbol, decision.notional)
            return decision

    def release_reservation(self, intent_id: str) -> bool:
        """OMS confirms terminal outcome. The ID remains replay-protected."""
        with self._lock:
            return self._reservations.pop(intent_id, None) is not None

    def trip_manual_kill(self, reason: str) -> bool:
        """Operator kill; does NOT cancel existing broker orders."""
        if not isinstance(reason, str) or not reason.strip():
            return False
        with self._lock:
            self._manual_reason = reason
            return True

    def clear_manual_kill(
        self, snapshot: PortfolioSnapshot, now: datetime, operator_reason: str
    ) -> bool:
        """Explicit acknowledgement with verified fresh state; daily kill stays latched."""
        with self._lock:
            if not self._manual_reason or not isinstance(operator_reason, str) or not operator_reason.strip():
                return False
            if validate_snapshot(snapshot, now, self.limits):
                return False
            if (to_decimal(snapshot.session_start_equity) - to_decimal(snapshot.equity)) / to_decimal(snapshot.session_start_equity) >= self.limits.daily_drawdown_fraction:
                return False
            self._manual_reason = None
            return True

    def advance_session(
        self, snapshot: PortfolioSnapshot, now: datetime, operator_reason: str
    ) -> bool:
        """Explicit session rollover after fresh broker reconciliation.

        Caller owns verifying the trusted exchange calendar, authentication
        and durable audit trail. Never silently untrip on an equity rebound.
        """
        with self._lock:
            if not isinstance(operator_reason, str) or not operator_reason.strip():
                return False
            if validate_snapshot(snapshot, now, self.limits):
                return False
            if self._active_session is None or snapshot.session_id == self._active_session:
                return False
            if self._reservations:
                return False
            if (to_decimal(snapshot.session_start_equity) - to_decimal(snapshot.equity)) / to_decimal(snapshot.session_start_equity) >= self.limits.daily_drawdown_fraction:
                return False
            self._active_session = snapshot.session_id
            self._daily_latched_session = None
            # The independent manual kill must be cleared separately.
            return True
