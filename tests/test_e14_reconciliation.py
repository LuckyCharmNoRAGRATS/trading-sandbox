"""E1.4 mocked reconciliation: no external Alpaca or Supabase connections."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import live_paper_trader
import dashboard_bridge


def command(status="submitting", **changes):
    record = dict(
        id=101, cmd="submit_order", status=status,
        claim_token="fake-token", client_order_id="e1-101",
        broker_order_id=None,
    )
    record.update(changes)
    return record


def harness(record, order=None):
    bridge = MagicMock()
    bridge.list_unresolved_trade_commands.return_value = [record]
    broker = MagicMock()
    broker.get_order_by_client_id.return_value = order or SimpleNamespace(
        client_order_id="e1-101", id="paper-42", status="accepted",
    )
    worker = live_paper_trader.LiveExecutionEngine.__new__(
        live_paper_trader.LiveExecutionEngine
    )
    worker.trading_client = broker
    return worker, bridge, broker


@pytest.mark.parametrize("initial", ["submitting", "uncertain"])
@pytest.mark.parametrize("broker_status", ["accepted", "partially_filled", "filled", "canceled", "expired", "rejected"])
def test_found_broker_order_persists_identity_but_remains_blocked(initial, broker_status):
    worker, bridge, broker = harness(
        command(initial),
        SimpleNamespace(client_order_id="e1-101", id="paper-42", status=broker_status),
    )
    assert worker.reconcile_unresolved_commands(bridge) == [(101, "matched_still_blocked")]
    broker.get_order_by_client_id.assert_called_once_with("e1-101")
    bridge.transition_claimed.assert_called_once()
    assert bridge.transition_claimed.call_args.args[1:3] == (initial, "acknowledged")
    assert bridge.transition_claimed.call_args.kwargs["broker_id"] == "paper-42"
    broker.submit_order.assert_not_called()


def test_acknowledged_order_remains_blocked_without_second_transition():
    worker, bridge, broker = harness(command("acknowledged", broker_order_id="paper-42"))
    assert worker.reconcile_unresolved_commands(bridge) == [(101, "matched_still_blocked")]
    bridge.transition_claimed.assert_not_called()
    broker.submit_order.assert_not_called()


def test_claimed_without_submitting_stays_for_manual_review():
    worker, bridge, broker = harness(command("claimed"))
    assert worker.reconcile_unresolved_commands(bridge) == [(101, "claimed_requires_review")]
    broker.get_order_by_client_id.assert_not_called()
    bridge.transition_claimed.assert_not_called()
    broker.submit_order.assert_not_called()


def test_broker_timeout_remains_unresolved_without_retry():
    worker, bridge, broker = harness(command("uncertain"))
    broker.get_order_by_client_id.side_effect = TimeoutError("offline")
    assert worker.reconcile_unresolved_commands(bridge) == [(101, "lookup_unresolved")]
    bridge.transition_claimed.assert_not_called()
    broker.submit_order.assert_not_called()


def test_broker_not_found_remains_unresolved_without_retry():
    worker, bridge, broker = harness(command("submitting"))
    broker.get_order_by_client_id.return_value = None
    assert worker.reconcile_unresolved_commands(bridge) == [(101, "lookup_unresolved")]
    bridge.transition_claimed.assert_not_called()
    broker.submit_order.assert_not_called()


@pytest.mark.parametrize("broker_record", [
    SimpleNamespace(client_order_id="e1-777", id="paper-42", status="accepted"),
    SimpleNamespace(client_order_id="e1-101", id=None, status="accepted"),
    SimpleNamespace(client_order_id="e1-101", id="paper-42", status=None),
])
def test_malformed_broker_response_fails_closed(broker_record):
    worker, bridge, broker = harness(command(), broker_record)
    with pytest.raises(RuntimeError, match="Ambiguous"):
        worker.reconcile_unresolved_commands(bridge)
    bridge.transition_claimed.assert_not_called()
    broker.submit_order.assert_not_called()


def test_broker_order_id_conflict_fails_closed():
    worker, bridge, broker = harness(command(broker_order_id="different-id"))
    with pytest.raises(RuntimeError, match="conflicts"):
        worker.reconcile_unresolved_commands(bridge)
    bridge.transition_claimed.assert_not_called()
    broker.submit_order.assert_not_called()


def test_broker_id_not_bound_to_command_fails_closed():
    worker, bridge, broker = harness(command(client_order_id="e1-999"))
    with pytest.raises(RuntimeError, match="Invalid unresolved"):
        worker.reconcile_unresolved_commands(bridge)
    broker.get_order_by_client_id.assert_not_called()
    broker.submit_order.assert_not_called()


def test_database_write_failure_never_submits_order():
    worker, bridge, broker = harness(command())
    bridge.transition_claimed.side_effect = RuntimeError("DB unavailable")
    with pytest.raises(RuntimeError, match="DB unavailable"):
        worker.reconcile_unresolved_commands(bridge)
    broker.submit_order.assert_not_called()


def test_multiple_records_are_each_matched_without_submission():
    first = command()
    second = command(id=102, client_order_id="e1-102", status="acknowledged")
    worker, bridge, broker = harness(first)
    bridge.list_unresolved_trade_commands.return_value = [first, second]
    broker.get_order_by_client_id.side_effect = [
        SimpleNamespace(client_order_id="e1-101", id="paper-a", status="new"),
        SimpleNamespace(client_order_id="e1-102", id="paper-b", status="filled"),
    ]
    assert len(worker.reconcile_unresolved_commands(bridge)) == 2
    broker.submit_order.assert_not_called()


def test_bridge_paginates_unresolved_orders_without_truncation():
    bridge = dashboard_bridge.DashboardBridge.__new__(dashboard_bridge.DashboardBridge)
    bridge.client = MagicMock()
    response = bridge.client.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.range.return_value.execute.return_value
    response.data = [dict(id=i) for i in range(100)]
    # Return a full page then a short final page.
    response2 = SimpleNamespace(data=[dict(id=100)])
    query = bridge.client.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.range.return_value.execute
    query.side_effect = [response, response2]
    rows = bridge.list_unresolved_trade_commands()
    assert len(rows) == 101
    ranges = bridge.client.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.range.call_args_list
    assert ranges[0].args == (0, 99)
    assert ranges[1].args == (100, 199)
