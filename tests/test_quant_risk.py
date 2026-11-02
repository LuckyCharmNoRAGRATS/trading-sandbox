"""Offline regression suite for quant_risk; no broker imports or network I/O."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quant_risk import (
    OrderIntent, PortfolioSnapshot, RiskDecision, RiskGate, RiskLimits, assess_order,
)


class QuantRiskTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
        self.snapshot = PortfolioSnapshot(
            equity=Decimal("100000"),
            session_start_equity=Decimal("100000"),
            gross_exposure=Decimal("0"),
            pending_exposure=Decimal("0"),
            positions=(),
            pending_symbols=(),
            as_of=self.now,
            session_id="2026-11-02",
        )
        self.intent = OrderIntent("test-1", "AAPL", "BUY", Decimal("100"),
                                  Decimal("90"), self.now)

    def test_sizing_notional_cap(self):
        d = assess_order(self.intent, self.snapshot, RiskLimits(), self.now)
        self.assertTrue(d.allowed)
        self.assertEqual(d.quantity, 50)
        self.assertEqual(d.notional, Decimal("5000"))
        self.assertEqual(d.projected_gross_exposure, Decimal("5000"))

    def test_sizing_stop_risk_cap(self):
        d = assess_order(replace(self.intent, stop_price=Decimal("50")),
                         self.snapshot, RiskLimits(), self.now)
        self.assertEqual(d.quantity, 20)

    def test_portfolio_remaining_capacity_scales_safe_size(self):
        snap = replace(self.snapshot, gross_exposure=Decimal("48000"),
                       positions=(("MSFT", Decimal("48000")),))
        self.assertEqual(assess_order(self.intent, snap, RiskLimits(), self.now).quantity, 20)

    def test_total_exposure_limit_rejects(self):
        snap = replace(self.snapshot, gross_exposure=Decimal("50000"),
                       positions=(("MSFT", Decimal("50000")),))
        self.assertEqual(assess_order(self.intent, snap, RiskLimits(), self.now).reason_code,
                         "PORTFOLIO_LIMIT")

    def test_pending_exposure_counts_against_limit(self):
        snap = replace(self.snapshot, pending_exposure=Decimal("49000"),
                       pending_symbols=("MSFT",))
        self.assertEqual(assess_order(self.intent, snap, RiskLimits(), self.now).quantity, 10)

    def test_requested_over_limit_is_rejected_instead_of_resized(self):
        d = assess_order(replace(self.intent, requested_quantity=51),
                         self.snapshot, RiskLimits(), self.now)
        self.assertEqual((d.allowed, d.reason_code, d.quantity),
                         (False, "LIMIT_BREACHED", 0))

    def test_requested_within_limit_preserved(self):
        self.assertEqual(assess_order(replace(self.intent, requested_quantity=3),
                                     self.snapshot, RiskLimits(), self.now).quantity, 3)

    def test_reject_invalid_price_and_nonfinite(self):
        for value in (0, -1, float("nan"), float("inf"), "100", True):
            with self.subTest(value=value):
                d = assess_order(replace(self.intent, entry_price=value),
                                 self.snapshot, RiskLimits(), self.now)
                self.assertFalse(d.allowed)
                self.assertEqual(d.quantity, 0)

    def test_wrong_direction_stop_and_zero_risk(self):
        for side, stop in (("BUY", 100), ("BUY", 110), ("SELL", 90), ("SELL", 100)):
            with self.subTest(side=side, stop=stop):
                d = assess_order(replace(self.intent, side=side, stop_price=stop),
                                 self.snapshot, RiskLimits(), self.now)
                self.assertEqual(d.reason_code, "INVALID_STOP")

    def test_sell_short_sized_with_valid_protective_stop(self):
        d = assess_order(replace(self.intent, side="SELL", stop_price=110),
                         self.snapshot, RiskLimits(), self.now)
        self.assertTrue(d.allowed)
        self.assertEqual(d.quantity, 50)

    def test_quote_staleness_and_future_quote_fail_closed(self):
        for lag in (timedelta(seconds=91), timedelta(seconds=-1)):
            d = assess_order(replace(self.intent, quote_at=self.now - lag),
                             self.snapshot, RiskLimits(), self.now)
            self.assertEqual(d.reason_code, "STALE_QUOTE")

    def test_snapshot_stale_or_future_rejected(self):
        for lag in (timedelta(seconds=91), timedelta(seconds=-1)):
            d = assess_order(self.intent,
                             replace(self.snapshot, as_of=self.now - lag),
                             RiskLimits(), self.now)
            self.assertEqual(d.reason_code, "STALE_SNAPSHOT")

    def test_invalid_snapshot_and_missing_pending_notional(self):
        self.assertEqual(assess_order(self.intent, replace(self.snapshot, equity=0),
                                      RiskLimits(), self.now).reason_code, "INVALID_SNAPSHOT")
        broken = replace(self.snapshot, pending_symbols=("MSFT",), pending_exposure=0)
        self.assertEqual(assess_order(self.intent, broken, RiskLimits(), self.now).reason_code,
                         "INVALID_SNAPSHOT")
        broken2 = replace(self.snapshot, gross_exposure=10,
                          positions=(("MSFT", Decimal("100")),))
        self.assertEqual(assess_order(self.intent, broken2, RiskLimits(), self.now).reason_code,
                         "INVALID_SNAPSHOT")

    def test_preexisting_position_or_pending_symbol_rejected(self):
        open_snap = replace(self.snapshot, gross_exposure=1000,
                            positions=(("AAPL", Decimal("1000")),))
        pend_snap = replace(self.snapshot, pending_exposure=1000,
                            pending_symbols=("AAPL",))
        for snapshot in (open_snap, pend_snap):
            self.assertEqual(assess_order(self.intent, snapshot, RiskLimits(), self.now).reason_code,
                             "SYMBOL_ALREADY_ACTIVE")

    def test_invalid_policy_rejected_at_creation(self):
        with self.assertRaises(ValueError):
            RiskLimits(trade_risk_fraction=Decimal("NaN"))
        with self.assertRaises(ValueError):
            RiskLimits(portfolio_gross_fraction=0)
        with self.assertRaises(ValueError):
            RiskLimits(max_quote_age_seconds=-1)

    def test_decision_is_immutable(self):
        decision = assess_order(self.intent, self.snapshot, RiskLimits(), self.now)
        with self.assertRaises(FrozenInstanceError):
            decision.quantity = 1000
        self.assertIsInstance(decision, RiskDecision)

    def test_gate_duplicates_by_id_even_after_release(self):
        gate = RiskGate()
        self.assertTrue(gate.preflight(self.intent, self.snapshot, self.now).allowed)
        self.assertEqual(gate.preflight(self.intent, self.snapshot, self.now).reason_code,
                         "DUPLICATE_INTENT")
        self.assertTrue(gate.release_reservation("test-1"))
        self.assertEqual(gate.preflight(self.intent, self.snapshot, self.now).reason_code,
                         "DUPLICATE_INTENT")

    def test_gate_reserves_symbol_and_notional(self):
        gate = RiskGate()
        self.assertTrue(gate.preflight(self.intent, self.snapshot, self.now).allowed)
        d = gate.preflight(replace(self.intent, intent_id="test-2"),
                           self.snapshot, self.now)
        self.assertEqual(d.reason_code, "SYMBOL_RESERVED")
        another = gate.preflight(replace(self.intent, intent_id="test-3", symbol="MSFT"),
                                 self.snapshot, self.now)
        self.assertTrue(another.allowed)
        self.assertEqual(gate.state.reserved_intents, 2)

    def test_concurrent_same_symbol_only_one_reservation(self):
        gate = RiskGate()
        intents = [replace(self.intent, intent_id=f"concurrent-{i}") for i in range(16)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(pool.map(lambda i: gate.preflight(i, self.snapshot, self.now), intents))
        self.assertEqual(sum(d.allowed for d in decisions), 1)

    def test_daily_breaker_latches_even_after_recovery(self):
        gate = RiskGate()
        down = replace(self.snapshot, equity=Decimal("97000"))
        self.assertEqual(gate.preflight(self.intent, down, self.now).reason_code,
                         "DAILY_DRAWDOWN")
        self.assertEqual(gate.state.daily_latched_session, "2026-11-02")
        self.assertEqual(gate.preflight(self.intent, self.snapshot, self.now).reason_code,
                         "DAILY_KILL_LATCHED")
        self.assertFalse(gate.advance_session(self.snapshot, self.now, "try early"))

    def test_daily_breaker_recovery_requires_new_session_and_fresh_snapshot(self):
        gate = RiskGate()
        down = replace(self.snapshot, equity=Decimal("96000"))
        gate.preflight(self.intent, down, self.now)
        next_day = self.now + timedelta(days=1)
        fresh = replace(self.snapshot, as_of=next_day, session_id="2026-11-03")
        self.assertFalse(gate.advance_session(fresh, self.now, "stale clock"))
        self.assertTrue(gate.advance_session(fresh, next_day, "broker reconciled"))
        self.assertTrue(gate.preflight(replace(self.intent, intent_id="new-session",
                                              quote_at=next_day), fresh, next_day).allowed)

    def test_kill_retains_unreconciled_reservations(self):
        gate = RiskGate()
        self.assertTrue(gate.preflight(self.intent, self.snapshot, self.now).allowed)
        gate.trip_manual_kill("operator emergency")
        self.assertEqual(gate.state.reserved_intents, 1)
        self.assertTrue(gate.clear_manual_kill(self.snapshot, self.now, "broker reconciled"))
        self.assertEqual(gate.preflight(replace(self.intent, intent_id="another"),
                                        self.snapshot, self.now).reason_code, "SYMBOL_RESERVED")
        self.assertTrue(gate.release_reservation("test-1"))

    def test_manual_kill_and_explicit_recovery(self):
        gate = RiskGate()
        self.assertTrue(gate.trip_manual_kill("operator emergency"))
        self.assertEqual(gate.preflight(self.intent, self.snapshot, self.now).reason_code,
                         "MANUAL_KILL")
        self.assertFalse(gate.clear_manual_kill(self.snapshot, self.now, ""))
        self.assertTrue(gate.clear_manual_kill(self.snapshot, self.now, "reconciled"))
        self.assertTrue(gate.preflight(self.intent, self.snapshot, self.now).allowed)

    def test_new_session_with_outstanding_reservation_is_denied(self):
        gate = RiskGate()
        self.assertTrue(gate.preflight(self.intent, self.snapshot, self.now).allowed)
        next_day = self.now + timedelta(days=1)
        fresh = replace(self.snapshot, as_of=next_day, session_id="2026-11-03")
        self.assertFalse(gate.advance_session(fresh, next_day, "not reconciled"))
        self.assertEqual(gate.preflight(replace(self.intent, intent_id="next",
                                               quote_at=next_day), fresh, next_day).reason_code,
                         "SESSION_CHANGE_REQUIRES_RESET")
        self.assertTrue(gate.release_reservation("test-1"))
        self.assertTrue(gate.advance_session(fresh, next_day, "reconciled"))


if __name__ == "__main__":
    unittest.main()
