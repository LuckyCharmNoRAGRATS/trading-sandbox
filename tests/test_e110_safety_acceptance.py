"""E1.10 execution safety acceptance: fault injection only, no network.

These tests exercise the *real worker method* with a temporary SQLite audit DB,
a mocked command bridge and a mocked broker. They do not prove broker atomicity
or authorize paper/live trading.
"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock
import sqlite3

import pytest

from execution_audit import ExecutionAuditStore, AuditIntegrityError
from live_paper_trader import LiveExecutionEngine
from quant_engine import QuantConfig, RiskEngine


def command():
    return {
        "id": 101, "cmd": "submit_order", "status": "claimed",
        "claim_token": "mock-owned", "client_order_id": "e1-101",
        "args": {"symbol": "SPY", "side": "BUY",
                 "entry_price": 100, "stop_loss": 95},
    }


def setup(tmp_path, audit_path=None):
    path = audit_path or tmp_path / "audit.sqlite3"
    worker = LiveExecutionEngine.__new__(LiveExecutionEngine)
    worker.audit_store = ExecutionAuditStore(path)
    broker = MagicMock()
    broker.submit_order.return_value = SimpleNamespace(id="fake-order")
    worker.trading_client = broker
    bridge = MagicMock()
    state = {
        "equity": 100000., "start_of_day_equity": 100000.,
        "open_symbols": [], "pending_symbols": [],
        "portfolio_exposure": 0.,
        "snapshot_verified": True,
        "position_quantities": {},
    }
    risk = RiskEngine(account_equity=100000, config=QuantConfig())
    return worker, broker, bridge, state, risk


def kinds(worker):
    return [e["kind"] for e in worker.audit_store.history(101)]


@pytest.mark.parametrize("failed_event", ["pre_submit_baseline", "submission_intent"])
def test_audit_write_failure_prevents_broker_call(tmp_path, failed_event):
    worker, broker, bridge, state, risk = setup(tmp_path)
    original = worker.audit_store.append

    def fail(command_id, client_id, event, payload):
        if event == failed_event:
            raise OSError("simulated disk failure")
        return original(command_id, client_id, event, payload)

    worker.audit_store.append = fail
    with pytest.raises(OSError, match="disk failure"):
        worker.process_claimed_order(command(), bridge, state, risk)
    broker.submit_order.assert_not_called()
    if failed_event == "pre_submit_baseline":
        bridge.transition_claimed.assert_not_called()
    else:
        bridge.transition_claimed.assert_called_once()


def test_broker_timeout_never_automatically_resubmits_on_restart(tmp_path):
    worker, broker, bridge, state, risk = setup(tmp_path)
    broker.submit_order.side_effect = TimeoutError("unknown broker outcome")
    with pytest.raises(TimeoutError):
        worker.process_claimed_order(command(), bridge, state, risk)
    assert kinds(worker)[-1] == "submission_uncertain"

    new_worker, second_broker, second_bridge, new_state, new_risk = setup(tmp_path)
    second_bridge.list_unresolved_trade_commands.return_value = [command()]
    report = new_worker.validate_restart_audit(second_bridge)
    assert report["clearance_authorized"] is False
    with pytest.raises(AuditIntegrityError, match="already has"):
        new_worker.process_claimed_order(command(), second_bridge, new_state, new_risk)
    broker.submit_order.assert_called_once()
    second_broker.submit_order.assert_not_called()


def test_crash_after_broker_acceptance_before_ack_does_not_retry(tmp_path):
    worker, broker, bridge, state, risk = setup(tmp_path)
    bridge.transition_claimed.side_effect = [True, RuntimeError("ack lost")]
    with pytest.raises(RuntimeError, match="ack lost"):
        worker.process_claimed_order(command(), bridge, state, risk)
    assert kinds(worker)[-1] == "broker_order_snapshot"
    assert broker.submit_order.call_count == 1

    restarted, second_broker, new_bridge, new_state, new_risk = setup(tmp_path)
    new_bridge.list_unresolved_trade_commands.return_value = [command()]
    assert restarted.validate_restart_audit(new_bridge)["clearance_authorized"] is False
    with pytest.raises(AuditIntegrityError, match="already has"):
        restarted.process_claimed_order(command(), new_bridge, new_state, new_risk)
    second_broker.submit_order.assert_not_called()


def test_database_crash_after_baseline_still_blocks_replay(tmp_path):
    worker, broker, bridge, state, risk = setup(tmp_path)
    bridge.transition_claimed.side_effect = ConnectionError("database offline")
    with pytest.raises(ConnectionError):
        worker.process_claimed_order(command(), bridge, state, risk)
    assert kinds(worker) == ["pre_submit_baseline"]
    restarted, replay_broker, other_bridge, new_state, new_risk = setup(tmp_path)
    with pytest.raises(AuditIntegrityError, match="already has"):
        restarted.process_claimed_order(command(), other_bridge, new_state, new_risk)
    replay_broker.submit_order.assert_not_called()


def test_corrupted_persistent_evidence_rejects_restart(tmp_path):
    worker, broker, bridge, state, risk = setup(tmp_path)
    worker.audit_store.append(101, "e1-101", "pre_submit_baseline",
                              {"client_order_id": "e1-101", "position_qty": "0"})
    with sqlite3.connect(str(worker.audit_store.path)) as conn:
        conn.execute("UPDATE events SET payload=? WHERE seq=1",
                     ('{"client_order_id":"e1-101","position_qty":"999"}',))
    restarted = LiveExecutionEngine.__new__(LiveExecutionEngine)
    restarted.audit_store = worker.audit_store
    bridge.list_unresolved_trade_commands.return_value = [command()]
    with pytest.raises(AuditIntegrityError, match="hash mismatch"):
        restarted.validate_restart_audit(bridge)
    broker.submit_order.assert_not_called()


def test_lost_local_database_with_unresolved_command_fails_closed(tmp_path):
    worker, broker, bridge, state, risk = setup(tmp_path)
    bridge.list_unresolved_trade_commands.return_value = [command()]
    with pytest.raises(AuditIntegrityError, match="Missing durable"):
        worker.validate_restart_audit(bridge)
    broker.submit_order.assert_not_called()


def test_two_workers_same_command_never_submit_twice(tmp_path):
    """SQLite permits one durable baseline; this is not a DB RPC race proof."""
    path = tmp_path / "shared.sqlite3"
    setup(tmp_path, path)
    broker = MagicMock()
    broker.submit_order.return_value = SimpleNamespace(id="fake-order")

    def launch(_):
        worker, _, bridge, state, risk = setup(tmp_path, path)
        worker.trading_client = broker
        try:
            worker.process_claimed_order(command(), bridge, state, risk)
            return "submitted"
        except (AuditIntegrityError, sqlite3.Error):
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch, range(2)))
    assert results.count("submitted") <= 1
    assert broker.submit_order.call_count <= 1


def test_no_execution_when_snapshot_unverified(tmp_path):
    worker, broker, bridge, state, risk = setup(tmp_path)
    state["snapshot_verified"] = False
    with pytest.raises(AuditIntegrityError, match="Unverified"):
        worker.process_claimed_order(command(), bridge, state, risk)
    broker.submit_order.assert_not_called()


def test_broker_submission_happens_only_after_intent_and_state_transition(tmp_path):
    worker, broker, bridge, state, risk = setup(tmp_path)

    def submit(_request):
        assert kinds(worker) == ["pre_submit_baseline", "submission_intent"]
        bridge.transition_claimed.assert_called_once()
        assert bridge.transition_claimed.call_args.args[1:3] == ("claimed", "submitting")
        return SimpleNamespace(id="fake-id")

    broker.submit_order.side_effect = submit
    worker.process_claimed_order(command(), bridge, state, risk)
    broker.submit_order.assert_called_once()


def test_worker_stays_in_disabled_mode(monkeypatch, tmp_path):
    import live_paper_trader
    worker, broker, bridge, state, risk = setup(tmp_path)
    monkeypatch.delenv("E1_EXECUTION_ENABLED", raising=False)

    class StopLoop(BaseException):
        pass

    def stop(_):
        raise StopLoop

    monkeypatch.setattr(live_paper_trader.time, "sleep", stop)
    worker.sync_portfolio_state = MagicMock()
    with pytest.raises(StopLoop):
        worker.execute_loop()
    broker.submit_order.assert_not_called()
    worker.sync_portfolio_state.assert_not_called()
