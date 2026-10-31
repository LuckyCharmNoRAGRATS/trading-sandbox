"""E1.11 release gate: pure env and mocked worker tests, no broker network."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from release_gate import ReleaseGateError, validate_execution_release
import live_paper_trader


def approved_env(tmp_path):
    db = tmp_path / "audit.sqlite3"
    db.write_bytes(b"placeholder; runtime SQLite verification is separate")
    return {
        "E1_EXECUTION_ENABLED": "true",
        "E1_RELEASE_APPROVED": "paper-only-reviewed",
        "E1_BROKER_MODE": "paper",
        "E1_RECONCILIATION_ENABLED": "true",
        "E1_AUDIT_DB_PATH": str(db),
    }


@pytest.mark.parametrize("enabled", [None, "", "false", "TRUE", "1", "True"])
def test_disabled_by_default(enabled):
    env = {}
    if enabled is not None:
        env["E1_EXECUTION_ENABLED"] = enabled
    assert validate_execution_release(env)["execution_allowed"] is False


@pytest.mark.parametrize("field,value", [
    ("E1_RELEASE_APPROVED", "true"),
    ("E1_RELEASE_APPROVED", ""),
    ("E1_BROKER_MODE", "live"),
    ("E1_RECONCILIATION_ENABLED", "false"),
    ("E1_AUDIT_DB_PATH", ""),
])
def test_missing_or_bad_precondition_rejected(tmp_path, field, value):
    env = approved_env(tmp_path)
    env[field] = value
    with pytest.raises(ReleaseGateError):
        validate_execution_release(env)


def test_existing_audit_file_required(tmp_path):
    env = approved_env(tmp_path)
    env["E1_AUDIT_DB_PATH"] = str(tmp_path / "never-created.sqlite3")
    with pytest.raises(ReleaseGateError, match="Existing"):
        validate_execution_release(env)


def test_relative_audit_path_rejected(tmp_path):
    env = approved_env(tmp_path)
    env["E1_AUDIT_DB_PATH"] = "relative.sqlite3"
    with pytest.raises(ReleaseGateError, match="absolute"):
        validate_execution_release(env)


def test_explicit_paper_only_config_accepts_config_not_trading(tmp_path):
    result = validate_execution_release(approved_env(tmp_path))
    assert result["execution_allowed"] is True
    assert "configuration" in result["reason"]


def test_worker_loop_does_not_touch_network_when_disabled(monkeypatch):
    monkeypatch.delenv("E1_EXECUTION_ENABLED", raising=False)
    worker = live_paper_trader.LiveExecutionEngine.__new__(
        live_paper_trader.LiveExecutionEngine
    )
    worker.trading_client = MagicMock()
    worker.sync_portfolio_state = MagicMock()
    class StopWorker(BaseException):
        pass
    monkeypatch.setattr(live_paper_trader.time, "sleep",
                        lambda _: (_ for _ in ()).throw(StopWorker()))
    with pytest.raises(StopWorker):
        worker.execute_loop()
    worker.trading_client.submit_order.assert_not_called()
    worker.trading_client.get_account.assert_not_called()
    worker.sync_portfolio_state.assert_not_called()


def test_worker_init_denies_explicit_release_without_paper_approval(monkeypatch):
    monkeypatch.setenv("E1_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("E1_RELEASE_APPROVED", raising=False)
    broker_constructor = MagicMock()
    monkeypatch.setattr(live_paper_trader, "TradingClient", broker_constructor)
    with pytest.raises(ReleaseGateError, match="approval"):
        live_paper_trader.LiveExecutionEngine()
    broker_constructor.assert_not_called()
