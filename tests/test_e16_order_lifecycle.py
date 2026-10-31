"""E1.6 pure order lifecycle unit tests: no broker or database clients."""
from decimal import Decimal
from types import SimpleNamespace as NS
import pytest

from order_lifecycle import (
    LifecycleError, normalize_order, compare_snapshots, reserve_open_order_notional,
)


def order(status="new", qty="10", filled_qty="0", filled_avg_price=None, **overrides):
    data = dict(
        client_order_id="e1-101", id="broker-77", symbol="SPY", side="buy",
        status=status, qty=qty, filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
    )
    data.update(overrides)
    return NS(**data)


def ledger(**changes):
    return normalize_order(order(**changes), "e1-101")


def test_new_order_holds_full_unfilled_quantity():
    state = ledger()
    assert state.remaining_quantity == Decimal("10")
    assert reserve_open_order_notional(state, "120") == Decimal("1200")
    assert state.requires_review


def test_partial_fill_counts_cumulative_remainder_once():
    first = ledger()
    second = ledger(status="partially_filled", filled_qty="4", filled_avg_price="100.25")
    third = ledger(status="partially_filled", filled_qty="4", filled_avg_price="100.25")
    assert compare_snapshots(first, second) == Decimal("4")
    assert compare_snapshots(second, third) == Decimal("0")
    assert second.remaining_quantity == Decimal("6")
    assert reserve_open_order_notional(second, "102") == Decimal("612")


def test_full_fill_has_zero_unfilled_remainder_but_is_still_review_blocked():
    state = ledger(status="filled", filled_qty="10", filled_avg_price="100")
    assert state.is_terminal
    assert state.requires_review
    assert reserve_open_order_notional(state, "101") == 0


@pytest.mark.parametrize("status", ["canceled", "expired", "rejected", "done_for_day"])
def test_terminal_partially_filled_order_retains_position_obligation(status):
    state = ledger(status=status, filled_qty="3", filled_avg_price="100")
    assert state.filled_quantity == Decimal("3")
    assert state.is_terminal and state.requires_review


@pytest.mark.parametrize("status", ["new", "accepted", "pending_cancel", "pending_replace", "suspended"])
def test_active_status_is_never_auto_released(status):
    state = ledger(status=status)
    assert not state.is_terminal
    assert state.requires_review


@pytest.mark.parametrize("bad", ["-1", "nan", "inf", "11"])
def test_invalid_filled_quantities_rejected(bad):
    with pytest.raises(LifecycleError):
        ledger(status="partially_filled", filled_qty=bad, filled_avg_price="100")


@pytest.mark.parametrize("bad", [None, "", "-1", "NaN", "inf", True])
def test_invalid_order_quantity_rejected(bad):
    with pytest.raises(LifecycleError):
        ledger(qty=bad)


def test_mismatched_client_id_rejected():
    with pytest.raises(LifecycleError, match="identity"):
        normalize_order(order(client_order_id="e1-999"), "e1-101")


def test_missing_fill_price_rejected():
    with pytest.raises(LifecycleError, match="average price"):
        ledger(status="partially_filled", filled_qty="4")


def test_overreported_fill_status_rejected():
    with pytest.raises(LifecycleError, match="contradicts"):
        ledger(status="filled", filled_qty="9", filled_avg_price="100")


def test_cumulative_fill_rollback_rejected():
    first = ledger(status="partially_filled", filled_qty="7", filled_avg_price="100")
    second = ledger(status="partially_filled", filled_qty="3", filled_avg_price="100")
    with pytest.raises(LifecycleError, match="backwards"):
        compare_snapshots(first, second)


def test_changed_broker_identity_rejected():
    previous = ledger()
    current = ledger(id="other")
    with pytest.raises(LifecycleError, match="identity"):
        compare_snapshots(previous, current)


def test_terminal_status_regression_rejected():
    previous = ledger(status="filled", filled_qty="10", filled_avg_price="100")
    current = ledger(status="canceled", filled_qty="10", filled_avg_price="100")
    with pytest.raises(LifecycleError, match="Terminal"):
        compare_snapshots(previous, current)


def test_replacement_quarantined():
    state = ledger(status="replaced")
    with pytest.raises(LifecycleError, match="Replaced"):
        reserve_open_order_notional(state, "100")


def test_fractional_shares_preserve_decimal_precision():
    state = ledger(status="partially_filled", qty="1.25", filled_qty="0.25", filled_avg_price="9.5")
    assert state.remaining_quantity == Decimal("1.00")
    assert reserve_open_order_notional(state, "10.20") == Decimal("10.2000")


def test_serialized_audit_values_are_strings():
    state = ledger(status="partially_filled", filled_qty="2", filled_avg_price="101")
    d = state.as_dict()
    assert d["remaining_quantity"] == "8"
    assert d["filled_avg_price"] == "101"
    assert d["requires_review"] is True
