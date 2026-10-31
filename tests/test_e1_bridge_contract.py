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
