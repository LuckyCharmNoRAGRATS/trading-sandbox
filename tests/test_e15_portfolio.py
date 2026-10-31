"""E1.5 pure risk-state checks: all broker data is mocked; no network."""
from types import SimpleNamespace as NS
import pytest

from portfolio_reconciliation import (
    PortfolioStateError, validate_broker_snapshot, assert_can_claim_new_trade,
)


def account(**changes):
    fields = dict(equity="100000", last_equity="100000", buying_power="50000")
    fields.update(changes)
    return NS(**fields)


def position(symbol="SPY", qty="10", market_value="1000"):
    return NS(symbol=symbol, qty=qty, market_value=market_value)


def order(symbol="SPY"):
    return NS(symbol=symbol)


def test_empty_portfolio_can_pass_snapshot_gate():
    state = validate_broker_snapshot(account(), [], [])
    assert state["portfolio_exposure"] == 0
    assert assert_can_claim_new_trade(state)


def test_long_and_short_exposure_are_added_absolutely():
    state = validate_broker_snapshot(
        account(), [position(), position("QQQ", "-12", "-2000")], [],
    )
    assert state["portfolio_exposure"] == 3000
    assert state["open_symbols"] == ["QQQ", "SPY"]


@pytest.mark.parametrize("bad", [None, "nan", "inf", "-1", "0", "", True])
def test_invalid_equity_fails_closed(bad):
    with pytest.raises(PortfolioStateError):
        validate_broker_snapshot(account(equity=bad), [], [])


@pytest.mark.parametrize("bad", [None, "nan", "inf", "-1", "", True])
def test_invalid_buying_power_fails_closed(bad):
    with pytest.raises(PortfolioStateError):
        validate_broker_snapshot(account(buying_power=bad), [], [])


@pytest.mark.parametrize("bad", [None, "nan", "inf", "0", "", True])
def test_invalid_position_value_fails_closed(bad):
    with pytest.raises(PortfolioStateError):
        validate_broker_snapshot(account(), [position(market_value=bad)], [])


@pytest.mark.parametrize("bad", [None, "nan", "inf", "0", "", True])
def test_invalid_position_quantity_fails_closed(bad):
    with pytest.raises(PortfolioStateError):
        validate_broker_snapshot(account(), [position(qty=bad)], [])


def test_duplicate_position_symbol_fails_closed():
    with pytest.raises(PortfolioStateError, match="Duplicate"):
        validate_broker_snapshot(account(), [position(), position()], [])


def test_excess_gross_exposure_fails_closed():
    with pytest.raises(PortfolioStateError, match="ceiling"):
        validate_broker_snapshot(account(), [position("SPY", "1", "-60000")], [])


def test_existing_broker_open_order_blocks_claim():
    state = validate_broker_snapshot(account(), [], [order()])
    assert state["pending_symbols"] == ["SPY"]
    assert not state["risk_increasing_orders_allowed"]
    with pytest.raises(PortfolioStateError, match="Open orders"):
        assert_can_claim_new_trade(state)


def test_missing_broker_collection_blocks_claim():
    with pytest.raises(PortfolioStateError, match="Incomplete"):
        validate_broker_snapshot(account(), [], None)


def test_unverified_snapshot_cannot_claim():
    with pytest.raises(PortfolioStateError):
        assert_can_claim_new_trade({})
