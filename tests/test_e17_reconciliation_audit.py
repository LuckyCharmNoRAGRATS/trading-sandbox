"""E1.7 read-only portfolio/order reconciliation; no external connections."""
from types import SimpleNamespace as NS
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from order_lifecycle import normalize_order
from reconciliation_audit import audit_order_position, ReconciliationError
from live_paper_trader import LiveExecutionEngine


def order(status="partially_filled", filled="3", **changes):
    fields = dict(
        id="broker-1", client_order_id="e1-101", symbol="SPY", side="buy",
        status=status, qty="10", filled_qty=filled,
        filled_avg_price="101" if Decimal(filled) > 0 else None,
    )
    fields.update(changes)
    return NS(**fields)


def audit(current=None, **options):
    args = dict(
        baseline_quantity="5",
        broker_positions=[NS(symbol="SPY", qty="8")],
        broker_open_orders=[NS(symbol="SPY")],
        exclusive_symbol_activity=True,
    )
    args.update(options)
    return audit_order_position(current or order(), "e1-101", **args)


def test_partial_fill_matches_signed_position_and_remains_blocked():
    result = audit()
    assert result["expected_position_quantity"] == "8"
    assert result["position_matches"]
    assert result["command_clearance_authorized"] is False
    assert result["risk_increasing_orders_allowed"] is False


def test_restart_repeated_poll_does_not_double_count():
    last = normalize_order(order(), "e1-101")
    result = audit(previous_order=last)
    assert result["filled_quantity"] == "3"
    assert result["expected_position_quantity"] == "8"


def test_position_mismatch_stays_blocked():
    result = audit(broker_positions=[NS(symbol="SPY", qty="7")])
    assert not result["position_matches"]
    assert not result["consistent_under_assumptions"]
    assert not result["command_clearance_authorized"]


def test_missing_exclusive_activity_proof_never_confirms_consistency():
    result = audit(exclusive_symbol_activity=False)
    assert result["position_matches"]
    assert not result["consistent_under_assumptions"]


def test_canceled_partial_fill_retains_three_shares():
    result = audit(order("canceled", "3"), broker_open_orders=[])
    assert result["lifecycle_terminal"]
    assert result["expected_position_quantity"] == "8"
    assert result["review_required"]
    assert not result["command_clearance_authorized"]


def test_terminal_order_still_in_open_orders_is_not_consistent():
    result = audit(order("filled", "10"), broker_positions=[NS(symbol="SPY", qty="15")])
    assert not result["consistent_under_assumptions"]


def test_sell_side_decreases_position():
    sell = order("filled", "10", side="sell")
    result = audit(sell, broker_positions=[NS(symbol="SPY", qty="-5")], broker_open_orders=[])
    assert result["expected_position_quantity"] == "-5"
    assert result["position_matches"]


@pytest.mark.parametrize("bad", [None, "nan", "inf", True, "oops"])
def test_missing_or_nonfinite_baseline_fails_closed(bad):
    with pytest.raises(ReconciliationError):
        audit(baseline_quantity=bad)


def test_incomplete_broker_snapshot_fails_closed():
    with pytest.raises(ReconciliationError, match="Incomplete"):
        audit(broker_positions=None)


def test_duplicate_positions_fail_closed():
    with pytest.raises(ReconciliationError, match="Duplicate"):
        audit(broker_positions=[NS(symbol="SPY", qty="8"), NS(symbol="SPY", qty="8")])


def test_cumulative_fill_rollback_fails_closed():
    previous = normalize_order(order("partially_filled", "5"), "e1-101")
    with pytest.raises(RuntimeError, match="backwards"):
        audit(previous_order=previous)


def test_broker_id_mismatch_fails_closed():
    with pytest.raises(RuntimeError, match="identity"):
        audit(order(id="different"), previous_order=normalize_order(order(), "e1-101"))


def test_worker_audit_does_not_submit_orders():
    worker = LiveExecutionEngine.__new__(LiveExecutionEngine)
    worker.trading_client = MagicMock()
    result = worker.audit_order_against_portfolio(
        order(), "e1-101", "5",
        [NS(symbol="SPY", qty="8")], [NS(symbol="SPY")],
        exclusive_symbol_activity=True,
    )
    assert result["command_clearance_authorized"] is False
    worker.trading_client.submit_order.assert_not_called()
    worker.trading_client.get_order_by_client_id.assert_not_called()
