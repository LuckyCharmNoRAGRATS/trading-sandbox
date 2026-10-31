"""E1 contract regression tests. No Supabase network access."""
from unittest.mock import MagicMock

import dashboard_bridge


def bridge_with_fake_client():
    bridge = dashboard_bridge.DashboardBridge.__new__(dashboard_bridge.DashboardBridge)
    bridge.client = MagicMock()
    bridge.client.table.return_value.upsert.return_value.execute.return_value.data = []
    bridge.client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
    bridge.client.table.return_value.insert.return_value.execute.return_value.data = []
    return bridge


def test_kv_upsert_uses_live_database_data_column():
    bridge = bridge_with_fake_client()
    assert bridge.set_kv("portfolio_state", {"equity": 100000}) is True
    bridge.client.table.assert_called_with("kv")
    bridge.client.table.return_value.upsert.assert_called_once_with({
        "key": "portfolio_state", "data": {"equity": 100000}
    })


def test_command_status_update_uses_result_not_nonexistent_message():
    bridge = bridge_with_fake_client()
    assert bridge.mark_command_processed(17, status="failed", message="Invalid order") is True
    bridge.client.table.return_value.update.assert_called_once_with({
        "status": "failed", "result": "Invalid order"
    })
    bridge.client.table.return_value.update.return_value.eq.assert_called_once_with("id", 17)


def test_log_handler_uses_existing_msg_and_ts_columns():
    import logging
    bridge = bridge_with_fake_client()
    original = dashboard_bridge.bridge
    dashboard_bridge.bridge = bridge
    try:
        handler = dashboard_bridge.DashboardLogHandler()
        handler.emit(logging.LogRecord("worker", logging.WARNING, __file__, 1, "example warning", (), None))
        bridge.client.table.assert_called_with("logs")
        payload = bridge.client.table.return_value.insert.call_args.args[0]
        assert payload["level"] == "WARNING"
        assert payload["msg"] == "example warning"
        assert "ts" in payload
        assert "message" not in payload and "timestamp" not in payload
    finally:
        dashboard_bridge.bridge = original


def test_bridge_returns_false_on_database_write_failure():
    bridge = bridge_with_fake_client()
    bridge.client.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("database unavailable")
    assert bridge.set_kv("portfolio_state", {"equity": 100000}) is False


def test_claim_rpc_fails_closed_without_backend():
    bridge = bridge_with_fake_client()
    bridge.client = None
    import pytest
    with pytest.raises(RuntimeError):
        bridge.claim_next_trade_command()


def test_claim_rpc_must_confirm_ownership(monkeypatch):
    import pytest
    bridge = bridge_with_fake_client()
    bridge.client.rpc.return_value.execute.return_value.data = [
        {"id": 5, "status": "claimed", "claim_token": "wrong"}
    ]
    with pytest.raises(RuntimeError, match="ownership"):
        bridge.claim_next_trade_command()


def test_transition_requires_single_matching_row():
    import pytest
    bridge = bridge_with_fake_client()
    bridge.client.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []
    cmd = {"id": 5, "claim_token": "fake-token"}
    with pytest.raises(RuntimeError, match="not confirmed"):
        bridge.transition_claimed(cmd, "claimed", "submitting")


def test_unresolved_lookup_includes_acknowledged_orders():
    bridge = bridge_with_fake_client()
    bridge.client.table.return_value.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
    bridge.unresolved_trade_commands()
    statuses = bridge.client.table.return_value.select.return_value.eq.return_value.in_.call_args.args[1]
    assert "acknowledged" in statuses
    assert "uncertain" in statuses
