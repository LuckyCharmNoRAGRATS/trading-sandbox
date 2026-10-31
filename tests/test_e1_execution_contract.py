"""E1.3.1 isolated worker contract regressions: no external broker or database access."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import live_paper_trader


def sample_command(side="BUY", **overrides):
    args = dict(symbol="SPY", side=side, entry_price=100.0, stop_loss=95.0)
    args.update(overrides)
    return dict(id=101, cmd="submit_order", args=args, status="claimed",
                claim_token="fake-claim-token", client_order_id="e1-101")


def fake_context():
    broker = MagicMock()
    broker.submit_order.return_value = SimpleNamespace(id="paper-test-order-1")
    bridge = MagicMock()
    bridge.transition_claimed.return_value = True
    risk = live_paper_trader.RiskEngine(
        account_equity=100000, config=live_paper_trader.QuantConfig()
    )
    worker = live_paper_trader.LiveExecutionEngine.__new__(
        live_paper_trader.LiveExecutionEngine
    )
    worker.trading_client = broker
    worker.audit_store = MagicMock()
    worker.audit_store.history.return_value = []
    state = dict(
        equity=100000.0, start_of_day_equity=100000.0,
        open_symbols=[], pending_symbols=[], portfolio_exposure=0.0,
        snapshot_verified=True, position_quantities={}
    )
    return worker, bridge, broker, state, risk


def test_canonical_cmd_args_reach_risk_checked_broker_request():
    worker, bridge, broker, state, risk = fake_context()
    worker.process_claimed_order(sample_command(), bridge, state, risk)
    broker.submit_order.assert_called_once()
    assert bridge.transition_claimed.call_count == 2
    assert bridge.transition_claimed.call_args.args[2] == "acknowledged"


@pytest.mark.parametrize("side", ["HOLD", "", "PURCHASE", None])
def test_invalid_side_fails_closed_without_order(side):
    worker, bridge, broker, state, risk = fake_context()
    worker.process_claimed_order(sample_command(side=side), bridge, state, risk)
    broker.submit_order.assert_not_called()
    bridge.transition_claimed.assert_called_once()
    assert bridge.transition_claimed.call_args.args[2] == "failed"


def test_invalid_stop_does_not_send_broker_order():
    worker, bridge, broker, state, risk = fake_context()
    worker.process_claimed_order(sample_command(stop_loss=100), bridge, state, risk)
    broker.submit_order.assert_not_called()
    assert bridge.transition_claimed.call_args.args[2] == "failed"


def test_broker_submission_must_have_stable_client_order_id():
    worker, bridge, broker, state, risk = fake_context()
    worker.process_claimed_order(sample_command(), bridge, state, risk)
    request = broker.submit_order.call_args.args[0]
    assert request.client_order_id == "e1-101"


def test_missing_claim_is_never_accepted():
    worker, bridge, broker, state, risk = fake_context()
    command = sample_command()
    command["status"] = "pending"
    with pytest.raises(ValueError):
        worker.process_claimed_order(command, bridge, state, risk)
    broker.submit_order.assert_not_called()


def test_missing_stable_id_fails_closed():
    worker, bridge, broker, state, risk = fake_context()
    command = sample_command()
    command["client_order_id"] = None
    with pytest.raises(ValueError):
        worker.process_claimed_order(command, bridge, state, risk)
    broker.submit_order.assert_not_called()


def test_database_transition_failure_prevents_submission():
    worker, bridge, broker, state, risk = fake_context()
    bridge.transition_claimed.side_effect = RuntimeError("database unreachable")
    with pytest.raises(RuntimeError):
        worker.process_claimed_order(sample_command(), bridge, state, risk)
    broker.submit_order.assert_not_called()


def test_submission_timeout_quarantines_and_never_retries():
    worker, bridge, broker, state, risk = fake_context()
    broker.submit_order.side_effect = TimeoutError("uncertain broker outcome")
    with pytest.raises(TimeoutError):
        worker.process_claimed_order(sample_command(), bridge, state, risk)
    broker.submit_order.assert_called_once()
    assert bridge.transition_claimed.call_args.args[2] == "uncertain"


def test_acknowledgement_db_failure_does_not_retry():
    worker, bridge, broker, state, risk = fake_context()
    bridge.transition_claimed.side_effect = [True, RuntimeError("ack write failed")]
    with pytest.raises(RuntimeError):
        worker.process_claimed_order(sample_command(), bridge, state, risk)
    broker.submit_order.assert_called_once()


def test_two_identical_pending_requests_must_not_submit_twice():
    """A duplicate cannot be claimed after the first became in-flight.

    Atomicity itself is to be proven against disposable PostgreSQL, not mocks.
    """
    worker, bridge, broker, state, risk = fake_context()
    first = sample_command()
    bridge.claim_next_trade_command.side_effect = [first, None]
    for _ in range(2):
        cmd = bridge.claim_next_trade_command()
        if cmd is not None:
            worker.process_claimed_order(cmd, bridge, state, risk)
    assert broker.submit_order.call_count == 1


def test_client_order_id_stays_same_across_worker_recreation():
    first_worker, first_bridge, first_broker, state, risk = fake_context()
    second_worker, second_bridge, second_broker, _, _ = fake_context()
    first_worker.process_claimed_order(sample_command(), first_bridge, state, risk)
    # Replay identity is stable, but must NOT be re-submitted after recovery.
    assert sample_command()["client_order_id"] == first_broker.submit_order.call_args.args[0].client_order_id
    second_broker.submit_order.assert_not_called()


class StopWorkerLoop(BaseException):
    """Escape the intentionally broad worker error handler during tests."""


def test_worker_disabled_by_default_does_not_contact_any_service(monkeypatch):
    monkeypatch.delenv("E1_EXECUTION_ENABLED", raising=False)
    monkeypatch.setattr(
        live_paper_trader.time, "sleep",
        lambda _: (_ for _ in ()).throw(StopWorkerLoop()),
    )
    worker, bridge, broker, state, risk = fake_context()
    worker.sync_portfolio_state = MagicMock()
    with pytest.raises(StopWorkerLoop):
        worker.execute_loop()
    worker.sync_portfolio_state.assert_not_called()
    broker.submit_order.assert_not_called()


def test_restart_with_unresolved_order_blocks_claim_and_broker(monkeypatch):
    monkeypatch.setenv("E1_EXECUTION_ENABLED", "true")
    monkeypatch.setattr(live_paper_trader, "DASHBOARD_SYNC", True)
    bridge = MagicMock()
    bridge.client = object()
    bridge.unresolved_trade_commands.return_value = [
        {"id": 101, "status": "submitting", "client_order_id": "e1-101"}
    ]
    monkeypatch.setattr(
        live_paper_trader, "dash", SimpleNamespace(bridge=bridge)
    )
    monkeypatch.setattr(
        live_paper_trader.time, "sleep",
        lambda _: (_ for _ in ()).throw(StopWorkerLoop()),
    )
    worker, _, broker, state, risk = fake_context()
    worker.sync_portfolio_state = MagicMock()
    with pytest.raises(StopWorkerLoop):
        worker.execute_loop()
    bridge.claim_next_trade_command.assert_not_called()
    broker.submit_order.assert_not_called()
    worker.sync_portfolio_state.assert_not_called()


def test_mismatched_broker_id_is_rejected_before_submission():
    worker, bridge, broker, state, risk = fake_context()
    command = sample_command()
    command["client_order_id"] = "e1-999"
    with pytest.raises(ValueError, match="stable broker"):
        worker.process_claimed_order(command, bridge, state, risk)
    broker.submit_order.assert_not_called()


def test_claim_without_owner_token_is_rejected_before_submission():
    worker, bridge, broker, state, risk = fake_context()
    command = sample_command()
    command["claim_token"] = None
    with pytest.raises(ValueError, match="stable broker"):
        worker.process_claimed_order(command, bridge, state, risk)
    broker.submit_order.assert_not_called()
