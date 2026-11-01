"""E1.12 independent bypass and recovery tests; broker, data and DB are mocked."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock
import sqlite3
import pytest

import live_paper_trader
from execution_audit import ExecutionAuditStore, AuditIntegrityError
from release_gate import ReleaseGateError, validate_execution_release
from quant_engine import QuantConfig, RiskEngine


def _command():
    return {
        "id": 101, "cmd": "submit_order", "status": "claimed",
        "claim_token": "fake-token", "client_order_id": "e1-101",
        "args": {"symbol": "SPY", "side": "BUY", "entry_price": 100, "stop_loss": 95},
    }


def _state():
    return {
        "equity": 100000.0, "start_of_day_equity": 100000.0,
        "open_symbols": [], "pending_symbols": [],
        "portfolio_exposure": 0.0, "snapshot_verified": True,
        "order_coverage_checked": True,
        "observationally_stable": True,
        "position_quantities": {},
    }


def _worker(path, broker):
    obj = live_paper_trader.LiveExecutionEngine.__new__(
        live_paper_trader.LiveExecutionEngine
    )
    obj.audit_store = ExecutionAuditStore(path)
    obj.trading_client = broker
    return obj


def test_constructor_disabled_never_constructs_broker_or_data(monkeypatch):
    monkeypatch.setenv("E1_EXECUTION_ENABLED", "false")
    broker_ctor = MagicMock()
    data_ctor = MagicMock()
    monkeypatch.setattr(live_paper_trader, "TradingClient", broker_ctor)
    monkeypatch.setattr(live_paper_trader, "DataLayer", data_ctor)
    worker = live_paper_trader.LiveExecutionEngine()
    assert worker.trading_client is None
    assert worker.data_layer is None
    broker_ctor.assert_not_called()
    data_ctor.assert_not_called()


def test_direct_submission_disabled_blocks_without_any_broker_call(tmp_path, monkeypatch):
    monkeypatch.setenv("E1_EXECUTION_ENABLED", "false")
    broker = MagicMock()
    obj = _worker(tmp_path / "audit.sqlite3", broker)
    bridge = MagicMock()
    risk = RiskEngine(account_equity=100000, config=QuantConfig())
    with pytest.raises(AuditIntegrityError, match="Release gate"):
        obj.process_claimed_order(_command(), bridge, _state(), risk)
    broker.submit_order.assert_not_called()
    bridge.transition_claimed.assert_not_called()


def test_direct_submission_enabled_without_approval_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("E1_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("E1_RELEASE_APPROVED", raising=False)
    broker = MagicMock()
    obj = _worker(tmp_path / "audit.sqlite3", broker)
    bridge = MagicMock()
    risk = RiskEngine(account_equity=100000, config=QuantConfig())
    with pytest.raises(ReleaseGateError, match="approval"):
        obj.process_claimed_order(_command(), bridge, _state(), risk)
    broker.submit_order.assert_not_called()


def test_runtime_gate_refuses_symlink_to_audit(tmp_path):
    target = tmp_path / "real.sqlite3"
    target.write_bytes(b"no client connection")
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(target)
    with pytest.raises(ReleaseGateError, match="safe absolute"):
        validate_execution_release({
            "E1_EXECUTION_ENABLED": "true",
            "E1_RELEASE_APPROVED": "paper-only-reviewed",
            "E1_BROKER_MODE": "paper",
            "E1_RECONCILIATION_ENABLED": "true",
            "E1_AUDIT_DB_PATH": str(alias),
        })


def test_concurrent_worker_gate_blocks_second_submission(tmp_path, monkeypatch):
    # Test-only approval override: broker is MagicMock, SQLite is temporary.
    monkeypatch.setattr(
        live_paper_trader, "validate_execution_release",
        lambda: {"execution_allowed": True},
    )
    path = tmp_path / "race.sqlite3"
    ExecutionAuditStore(path)
    broker = MagicMock()
    broker.submit_order.return_value = SimpleNamespace(id="mock-1")
    risk = RiskEngine(account_equity=100000, config=QuantConfig())

    def invoke(_):
        worker = _worker(path, broker)
        try:
            worker.process_claimed_order(_command(), MagicMock(), _state(), risk)
            return "submitted"
        except (AuditIntegrityError, sqlite3.Error):
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, range(2)))
    assert outcomes.count("submitted") <= 1
    assert broker.submit_order.call_count <= 1


def test_corrupted_audit_rejects_startup_and_broker_unused(tmp_path, monkeypatch):
    broker = MagicMock()
    path = tmp_path / "audit.sqlite3"
    worker = _worker(path, broker)
    worker.audit_store.append(
        101, "e1-101", "pre_submit_baseline",
        {"client_order_id": "e1-101", "position_qty": "0"},
    )
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE events SET payload='{}' WHERE seq=1")
    bridge = MagicMock()
    bridge.list_unresolved_trade_commands.return_value = [{
        "id": 101, "client_order_id": "e1-101",
    }]
    with pytest.raises(AuditIntegrityError):
        worker.validate_restart_audit(bridge)
    broker.submit_order.assert_not_called()


def test_no_valid_audit_path_does_not_grant_execution():
    with pytest.raises(ReleaseGateError):
        validate_execution_release({
            "E1_EXECUTION_ENABLED": "true",
            "E1_RELEASE_APPROVED": "paper-only-reviewed",
            "E1_BROKER_MODE": "paper",
            "E1_RECONCILIATION_ENABLED": "true",
            "E1_AUDIT_DB_PATH": "/not-an-existing-e1-directory/audit.sqlite3",
        })
