"""Offline regression contracts for the two broker-order execution paths.

Known defects are strict xfails until a later PR fixes production code.  No
Alpaca client is constructed and no external side effect is authorized.
"""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import quant_engine


class StopAfterOneTick(Exception):
    """Stop the infinite loop after its first iteration without waiting."""


def _stop_after_tick(_seconds):
    raise StopAfterOneTick


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Manual dashboard commands currently reference risk_engine before initialization.",
)
def test_dashboard_order_reaches_initialized_risk_gate_before_submitting(monkeypatch):
    """A pending manual order must be risk-checked even when the risk gate rejects it."""
    bridge = Mock()
    bridge.client = object()
    bridge.get_pending_commands.return_value = [
        {
            "id": "offline-test-command",
            "command": "submit_order",
            "payload": {
                "symbol": "SPY",
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "side": "buy",
            },
        }
    ]
    fake_dashboard = SimpleNamespace(bridge=bridge)
    # Never initialize a real Supabase client just to import the live loop.
    monkeypatch.setitem(sys.modules, "dashboard_bridge", fake_dashboard)
    import live_paper_trader as live

    monkeypatch.setattr(live, "dash", fake_dashboard)
    monkeypatch.setattr(live, "DASHBOARD_SYNC", True)
    monkeypatch.setattr(live.time, "sleep", _stop_after_tick)
    status_alert = Mock()
    error_alert = Mock()
    monkeypatch.setattr(live, "notify_system_status", status_alert)
    monkeypatch.setattr(live, "notify_error", error_alert)

    risk_policy = Mock()
    risk_policy.validate_and_size_order.return_value = 0
    make_risk_policy = Mock(return_value=risk_policy)
    monkeypatch.setattr(live, "RiskEngine", make_risk_policy)

    # Deliberately skip __init__: it constructs an Alpaca TradingClient.
    engine = live.LiveExecutionEngine.__new__(live.LiveExecutionEngine)
    engine.config = quant_engine.QuantConfig(
        api_key="", secret_key="", discord_webhook=""
    )
    engine.trading_client = Mock()
    engine.watchlist = []
    engine.sync_portfolio_state = Mock(return_value={
        "equity": 100000.0,
        "start_of_day_equity": 100000.0,
        "portfolio_exposure": 0.0,
        "open_symbols": [],
        "pending_symbols": [],
    })

    with pytest.raises(StopAfterOneTick):
        engine.execute_loop()

    make_risk_policy.assert_called_once()
    risk_policy.validate_and_size_order.assert_called_once()
    bridge.mark_command_processed.assert_called_once()
    engine.trading_client.submit_order.assert_not_called()
    error_alert.assert_not_called()


def test_automated_entry_uses_pre_trade_validation_without_broker_io(monkeypatch):
    """The currently implemented automatic entry path already invokes the full gate."""
    # Stub dashboard import so this test does not open a Supabase connection.
    monkeypatch.setitem(
        sys.modules,
        "dashboard_bridge",
        SimpleNamespace(bridge=Mock(client=None)),
    )
    import live_paper_trader as live

    monkeypatch.setattr(live, "DASHBOARD_SYNC", False)
    monkeypatch.setattr(live.time, "sleep", _stop_after_tick)
    monkeypatch.setattr(live, "notify_system_status", Mock())
    error_alert = Mock()
    monkeypatch.setattr(live, "notify_error", error_alert)

    risk_policy = Mock()
    risk_policy.validate_and_size_order.return_value = 0
    monkeypatch.setattr(live, "RiskEngine", Mock(return_value=risk_policy))

    engine = live.LiveExecutionEngine.__new__(live.LiveExecutionEngine)
    engine.config = quant_engine.QuantConfig(
        api_key="", secret_key="", discord_webhook=""
    )
    engine.trading_client = Mock()
    engine.meta_model = None
    engine.watchlist = ["SPY"]
    engine.sync_portfolio_state = Mock(return_value={
        "equity": 100000.0,
        "start_of_day_equity": 100000.0,
        "portfolio_exposure": 0.0,
        "open_symbols": [],
        "pending_symbols": [],
    })
    engine.generate_live_signals = Mock(
        return_value={"action": "BUY", "price": 100.0, "stop": 95.0}
    )

    with pytest.raises(StopAfterOneTick):
        engine.execute_loop()

    risk_policy.validate_and_size_order.assert_called_once()
    engine.trading_client.submit_order.assert_not_called()
    error_alert.assert_not_called()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ExecutionOMS currently calls size_position, bypassing validate_and_size_order.",
)
def test_oms_entry_respects_full_pre_trade_validation(monkeypatch):
    """The secondary OMS must reject a trade rejected by the central risk gate."""
    broker = Mock()
    broker.get_account.return_value = SimpleNamespace(
        equity="100000", last_equity="100000", trading_blocked=False
    )
    broker.submit_order.return_value = SimpleNamespace(id="offline-order")

    oms = quant_engine.ExecutionOMS.__new__(quant_engine.ExecutionOMS)
    oms.config = quant_engine.QuantConfig(
        api_key="", secret_key="", discord_webhook=""
    )
    oms.trading_client = broker
    oms.send_discord_alert = Mock()

    risk_policy = Mock()
    risk_policy.check_daily_circuit_breaker.return_value = True
    risk_policy.size_position.return_value = 10
    risk_policy.validate_and_size_order.return_value = 0
    monkeypatch.setattr(quant_engine, "RiskEngine", Mock(return_value=risk_policy))

    result = oms.execute_signal(
        symbol="SPY", entry_price=100.0, stop_loss=95.0,
        side=quant_engine.OrderSide.BUY,
    )

    risk_policy.validate_and_size_order.assert_called_once()
    broker.submit_order.assert_not_called()
    assert result is None
