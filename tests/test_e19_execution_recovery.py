"""E1.9 fail-closed evidence integration; never contact a real broker."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

@pytest.fixture(autouse=True)
def _mock_paper_release_for_isolated_contracts(monkeypatch):
    # Tests use mocked broker clients only; never enable real execution.
    import live_paper_trader
    from release_gate import validate_execution_release
    monkeypatch.setattr(
        live_paper_trader, "validate_execution_release",
        lambda: {"execution_allowed": True, "reason": "mock test only"},
    )
    yield
    monkeypatch.setattr(live_paper_trader, "validate_execution_release",
                        validate_execution_release)


from execution_audit import ExecutionAuditStore, AuditIntegrityError
from live_paper_trader import LiveExecutionEngine
from quant_engine import QuantConfig, RiskEngine


def command():
    return dict(
        id=101, cmd="submit_order", status="claimed", claim_token="mock-token",
        client_order_id="e1-101", args={
            "symbol": "SPY", "side": "BUY",
            "entry_price": 100, "stop_loss": 95,
        },
    )


def fixture(tmp_path):
    worker = LiveExecutionEngine.__new__(LiveExecutionEngine)
    broker = MagicMock()
    broker.submit_order.return_value = SimpleNamespace(id="mock-order-id")
    worker.trading_client = broker
    worker.audit_store = ExecutionAuditStore(tmp_path / "audit.sqlite3")
    bridge = MagicMock()
    state = dict(
        equity=100000, start_of_day_equity=100000,
        portfolio_exposure=0, open_symbols=[], pending_symbols=[],
        snapshot_verified=True, position_quantities={},
    )
    risk = RiskEngine(account_equity=100000, config=QuantConfig())
    return worker, broker, bridge, state, risk


def test_order_records_baseline_and_intent_before_broker_call(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    def broker_call(_):
        kinds = [e["kind"] for e in worker.audit_store.history(101)]
        assert kinds == ["pre_submit_baseline", "submission_intent"]
        bridge.transition_claimed.assert_called_once()
        return SimpleNamespace(id="mock-accepted")
    broker.submit_order.side_effect = broker_call
    worker.process_claimed_order(command(), bridge, state, risk)
    kinds = [e["kind"] for e in worker.audit_store.history(101)]
    assert kinds == ["pre_submit_baseline", "submission_intent", "broker_order_snapshot"]


def test_restart_detects_absent_audit_history(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    bridge.list_unresolved_trade_commands.return_value = [command()]
    with pytest.raises(AuditIntegrityError, match="Missing durable"):
        worker.validate_restart_audit(bridge)
    broker.submit_order.assert_not_called()


def test_restart_with_baseline_remains_blocked(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    worker.audit_store.append(101, "e1-101", "pre_submit_baseline",
                              {"client_order_id": "e1-101", "position_qty": "0"})
    bridge.list_unresolved_trade_commands.return_value = [command()]
    report = worker.validate_restart_audit(bridge)
    assert report["unresolved_count"] == 1
    assert report["clearance_authorized"] is False
    broker.submit_order.assert_not_called()


def test_missing_persistent_store_blocks_broker(tmp_path, monkeypatch):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    del worker.audit_store
    monkeypatch.delenv("E1_AUDIT_DB_PATH", raising=False)
    with pytest.raises(AuditIntegrityError, match="E1_AUDIT_DB_PATH"):
        worker.process_claimed_order(command(), bridge, state, risk)
    broker.submit_order.assert_not_called()


def test_invalid_snapshot_blocks_without_baseline(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    state["snapshot_verified"] = False
    with pytest.raises(AuditIntegrityError, match="Unverified"):
        worker.process_claimed_order(command(), bridge, state, risk)
    assert worker.audit_store.verify()["event_count"] == 0
    broker.submit_order.assert_not_called()


def test_db_pre_submit_transition_failure_never_contacts_broker(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    bridge.transition_claimed.side_effect = RuntimeError("db unavailable")
    with pytest.raises(RuntimeError, match="db unavailable"):
        worker.process_claimed_order(command(), bridge, state, risk)
    assert [e["kind"] for e in worker.audit_store.history(101)] == ["pre_submit_baseline"]
    broker.submit_order.assert_not_called()


def test_audit_intent_failure_never_contacts_broker(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    original = worker.audit_store.append
    def failure(command_id, client_id, kind, data):
        if kind == "submission_intent":
            raise AuditIntegrityError("disk unavailable")
        return original(command_id, client_id, kind, data)
    worker.audit_store.append = failure
    with pytest.raises(AuditIntegrityError, match="disk unavailable"):
        worker.process_claimed_order(command(), bridge, state, risk)
    broker.submit_order.assert_not_called()


def test_broker_timeout_persists_uncertain_evidence(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    broker.submit_order.side_effect = TimeoutError("transport lost")
    with pytest.raises(TimeoutError):
        worker.process_claimed_order(command(), bridge, state, risk)
    kinds = [e["kind"] for e in worker.audit_store.history(101)]
    assert kinds[-1] == "submission_uncertain"
    broker.submit_order.assert_called_once()


def test_replay_of_existing_history_is_rejected(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    worker.audit_store.append(101, "e1-101", "pre_submit_baseline",
                              {"client_order_id": "e1-101", "position_qty": "0"})
    with pytest.raises(AuditIntegrityError, match="already has"):
        worker.process_claimed_order(command(), bridge, state, risk)
    broker.submit_order.assert_not_called()


def test_invalid_baseline_identity_blocks_recovery(tmp_path):
    worker, broker, bridge, state, risk = fixture(tmp_path)
    worker.audit_store.append(101, "e1-101", "pre_submit_baseline",
                              {"client_order_id": "e1-999", "position_qty": "0"})
    bridge.list_unresolved_trade_commands.return_value = [command()]
    with pytest.raises(AuditIntegrityError, match="identity mismatch"):
        worker.validate_restart_audit(bridge)
    broker.submit_order.assert_not_called()
