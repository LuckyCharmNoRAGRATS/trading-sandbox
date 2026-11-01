"""E1.13 isolated broker-state coverage tests; all broker responses are mocked."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from broker_snapshot import (
    BrokerSnapshotError, MAX_OPEN_ORDERS_PER_PAGE,
    collect_stable_portfolio_snapshot,
)
from portfolio_reconciliation import PortfolioStateError, assert_can_claim_new_trade
import live_paper_trader


def account(equity="100000"):
    return NS(equity=equity, last_equity="100000", buying_power="100000")


def position(qty="10", value="1000"):
    return NS(symbol="SPY", qty=qty, market_value=value)


def order(i, *, status="new"):
    return NS(id=str(i), client_order_id=f"mock-{i}", symbol="SPY",
              status=status, side="buy", qty="1", filled_qty="0", legs=None)


def client_for(first_orders=None, second_orders=None, positions=None):
    client = MagicMock()
    client.get_account.side_effect = [account(), account()]
    client.get_all_positions.side_effect = [positions or [], positions or []]
    first = [] if first_orders is None else first_orders
    second = first if second_orders is None else second_orders
    client.get_orders.side_effect = [first, second]
    return client


def test_explicit_max_open_query_and_repeat_reads():
    broker = client_for()
    state = collect_stable_portfolio_snapshot(broker)
    assert state["order_coverage_checked"] is True
    assert state["observationally_stable"] is True
    assert state["open_order_count"] == 0
    assert assert_can_claim_new_trade(state)
    assert broker.get_orders.call_count == 2
    for call in broker.get_orders.call_args_list:
        request = call.kwargs["filter"]
        assert request.limit == MAX_OPEN_ORDERS_PER_PAGE
        assert request.nested is False
        assert request.status.value == "open"


def test_open_order_blocks_new_risk():
    state = collect_stable_portfolio_snapshot(client_for([order(1)]))
    assert state["pending_symbols"] == ["SPY"]
    with pytest.raises(PortfolioStateError):
        assert_can_claim_new_trade(state)


def test_full_page_blocks_as_possibly_truncated():
    broker = client_for([order(i) for i in range(MAX_OPEN_ORDERS_PER_PAGE)])
    with pytest.raises(BrokerSnapshotError, match="limit"):
        collect_stable_portfolio_snapshot(broker)
    assert broker.get_orders.call_count == 1


def test_page_below_cap_is_not_inherently_rejected():
    broker = client_for([order(i) for i in range(5)])
    state = collect_stable_portfolio_snapshot(broker)
    assert state["open_order_count"] == 5


def test_malformed_or_raw_order_collection_fails_closed():
    broker = client_for([])
    broker.get_orders.side_effect = [dict(orders=[])]
    with pytest.raises(BrokerSnapshotError, match="complete list"):
        collect_stable_portfolio_snapshot(broker)


def test_duplicate_order_id_fails_closed():
    with pytest.raises(BrokerSnapshotError, match="duplicate"):
        collect_stable_portfolio_snapshot(client_for([order(1), order(1)]))


def test_missing_order_id_fails_closed():
    with pytest.raises(BrokerSnapshotError, match="identity"):
        collect_stable_portfolio_snapshot(client_for([NS(symbol="SPY", status="new")]))


def test_terminal_order_in_open_response_fails_closed():
    with pytest.raises(BrokerSnapshotError, match="terminal"):
        collect_stable_portfolio_snapshot(client_for([order(1, status="filled")]))


def test_multi_leg_open_order_fails_closed():
    compound = order(1)
    compound.legs = [order(2)]
    with pytest.raises(BrokerSnapshotError, match="multi-leg"):
        collect_stable_portfolio_snapshot(client_for([compound]))


def test_open_order_changes_between_reads_blocks():
    broker = client_for([order(1)], [order(2)])
    with pytest.raises(BrokerSnapshotError, match="changed between"):
        collect_stable_portfolio_snapshot(broker)


def test_account_changes_between_reads_blocks():
    broker = client_for()
    broker.get_account.side_effect = [account("100000"), account("99999")]
    with pytest.raises(BrokerSnapshotError, match="changed between"):
        collect_stable_portfolio_snapshot(broker)


def test_position_changes_between_reads_blocks():
    broker = client_for(positions=[position()])
    broker.get_all_positions.side_effect = [
        [position("10", "1000")], [position("11", "1100")],
    ]
    with pytest.raises(BrokerSnapshotError, match="changed between"):
        collect_stable_portfolio_snapshot(broker)


def test_api_exception_propagates_and_blocks():
    broker = client_for()
    broker.get_orders.side_effect = TimeoutError("simulated broker timeout")
    with pytest.raises(TimeoutError):
        collect_stable_portfolio_snapshot(broker)


def test_worker_uses_coverage_checker_with_mock_broker(monkeypatch):
    monkeypatch.setattr(live_paper_trader, "DASHBOARD_SYNC", False)
    worker = live_paper_trader.LiveExecutionEngine.__new__(
        live_paper_trader.LiveExecutionEngine
    )
    worker.trading_client = client_for(positions=[position()])
    worker.config = NS(max_portfolio_exposure_pct=0.5)
    state = worker.sync_portfolio_state()
    assert state["order_coverage_checked"] is True
    assert state["position_quantities"]["SPY"] == "10"
    assert worker.trading_client.get_orders.call_count == 2
    worker.trading_client.submit_order.assert_not_called()


def test_direct_order_rejects_missing_coverage_flags(monkeypatch, tmp_path):
    from execution_audit import ExecutionAuditStore, AuditIntegrityError
    from quant_engine import QuantConfig, RiskEngine
    monkeypatch.setattr(
        live_paper_trader, "validate_execution_release",
        lambda: {"execution_allowed": True, "reason": "mock test only"},
    )
    worker = live_paper_trader.LiveExecutionEngine.__new__(
        live_paper_trader.LiveExecutionEngine
    )
    worker.audit_store = ExecutionAuditStore(tmp_path / "audit.sqlite3")
    worker.trading_client = MagicMock()
    bridge = MagicMock()
    state = {
        "equity": 100000, "start_of_day_equity": 100000,
        "open_symbols": [], "pending_symbols": [], "portfolio_exposure": 0,
        "position_quantities": {}, "snapshot_verified": True,
    }
    command = {
        "id": 101, "cmd": "submit_order", "status": "claimed",
        "claim_token": "mock", "client_order_id": "e1-101",
        "args": {"symbol": "SPY", "side": "BUY",
                 "entry_price": 100, "stop_loss": 95},
    }
    risk = RiskEngine(account_equity=100000, config=QuantConfig())
    with pytest.raises(AuditIntegrityError, match="coverage"):
        worker.process_claimed_order(command, bridge, state, risk)
    worker.trading_client.submit_order.assert_not_called()
    bridge.transition_claimed.assert_not_called()
