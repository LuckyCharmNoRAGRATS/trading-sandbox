"""Integration regressions for the recovered E1 command interface.
The retired legacy OMS must never construct a client or submit an order.
All clients are mocks; no execution permission is granted by these tests.
"""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import quant_engine
import live_paper_trader as live

class StopAfterOneTick(BaseException):
    pass

def _stop_after_tick(_seconds):
    raise StopAfterOneTick()

def _loop_context(monkeypatch, command):
    bridge = Mock(client=object())
    bridge.unresolved_trade_commands.return_value = []
    bridge.claim_next_trade_command.return_value = command
    monkeypatch.setattr(live, "dash", SimpleNamespace(bridge=bridge))
    monkeypatch.setattr(live, "DASHBOARD_SYNC", True)
    monkeypatch.setattr(live, "validate_execution_release",
                        lambda: {"execution_allowed": True})
    monkeypatch.setattr(live.time, "sleep", _stop_after_tick)
    worker = live.LiveExecutionEngine.__new__(live.LiveExecutionEngine)
    worker.config = quant_engine.QuantConfig(api_key="", secret_key="", discord_webhook="")
    worker.trading_client = Mock()
    worker.data_layer = Mock()
    worker.validate_restart_audit = Mock()
    worker.sync_portfolio_state = Mock(return_value={
        "equity": 100000.0, "start_of_day_equity": 100000.0,
        "portfolio_exposure": 0.0, "open_symbols": [], "pending_symbols": [],
        "risk_increasing_orders_allowed": True, "open_order_count": 0,
    })
    return worker, bridge

def test_dashboard_order_reaches_initialized_risk_gate_before_submitting(monkeypatch):
    command = {"id": 101, "cmd": "submit_order", "status": "claimed",
               "claim_token": "mock-token", "client_order_id": "e1-101",
               "args": {"symbol": "SPY", "side": "BUY",
                        "entry_price": 100.0, "stop_loss": 95.0}}
    worker, bridge = _loop_context(monkeypatch, command)
    risk = Mock()
    risk.validate_and_size_order.return_value = 0
    factory = Mock(return_value=risk)
    monkeypatch.setattr(live, "RiskEngine", factory)
    # Observe ordering without replacing the production command handler.
    def claim():
        factory.assert_called_once_with(account_equity=100000.0, config=worker.config)
        return command
    bridge.claim_next_trade_command.side_effect = claim
    with pytest.raises(StopAfterOneTick):
        worker.execute_loop()
    risk.validate_and_size_order.assert_called_once_with(
        symbol="SPY", entry_price=100.0, stop_loss_price=95.0,
        open_positions=[], pending_orders=[], current_portfolio_exposure=0.0,
        start_of_day_equity=100000.0)
    bridge.transition_claimed.assert_called_once()
    assert bridge.transition_claimed.call_args.args[:3] == (command, "claimed", "failed")
    worker.trading_client.submit_order.assert_not_called()

def test_automated_entries_remain_disabled_in_recovered_worker(monkeypatch):
    worker, bridge = _loop_context(monkeypatch, None)
    worker.watchlist = ["SPY"]
    worker.generate_live_signals = Mock(return_value={"action": "BUY", "price": 100, "stop": 95})
    with pytest.raises(StopAfterOneTick):
        worker.execute_loop()
    bridge.claim_next_trade_command.assert_called_once()
    worker.generate_live_signals.assert_not_called()
    worker.trading_client.submit_order.assert_not_called()
    worker.trading_client.close_position.assert_not_called()

@pytest.mark.parametrize("enabled", ["false", "true"])
def test_retired_oms_constructor_never_creates_broker_client(monkeypatch, enabled):
    monkeypatch.setenv("E1_EXECUTION_ENABLED", enabled)
    broker_factory = Mock(side_effect=AssertionError("Must not construct broker"))
    monkeypatch.setattr(quant_engine, "TradingClient", broker_factory)
    oms = quant_engine.ExecutionOMS(quant_engine.QuantConfig(
        api_key="synthetic", secret_key="synthetic", discord_webhook=""))
    assert oms.trading_client is None
    broker_factory.assert_not_called()


@pytest.mark.parametrize("enabled", ["false", "true"])
@pytest.mark.parametrize("side", [quant_engine.OrderSide.BUY, quant_engine.OrderSide.SELL])
def test_retired_oms_cannot_bypass_command_workflow(monkeypatch, enabled, side):
    monkeypatch.setenv("E1_EXECUTION_ENABLED", enabled)
    monkeypatch.setenv("E1_RELEASE_APPROVED", "true")
    # Also cover old instances with a client already attached.
    oms = quant_engine.ExecutionOMS.__new__(quant_engine.ExecutionOMS)
    broker = Mock()
    oms.trading_client = broker
    oms.send_discord_alert = Mock()
    with pytest.raises(quant_engine.LegacyExecutionDisabledError, match="retired"):
        oms.execute_signal("SPY", 100.0, 95.0, side)
    assert broker.mock_calls == []
    oms.send_discord_alert.assert_not_called()


def test_retired_oms_rejects_before_reading_instance_state():
    oms = quant_engine.ExecutionOMS.__new__(quant_engine.ExecutionOMS)
    with pytest.raises(quant_engine.LegacyExecutionDisabledError, match="No order was submitted"):
        oms.execute_signal("SPY", 100.0, 95.0, quant_engine.OrderSide.BUY)


def test_stats_import_does_not_fetch_even_when_cache_is_available(monkeypatch):
    import importlib
    import data_fetcher
    import stats_analyzer
    fetch = Mock(side_effect=AssertionError("Import must not load market data"))
    monkeypatch.setattr(data_fetcher, "get_historical_bars", fetch)
    importlib.reload(stats_analyzer)
    fetch.assert_not_called()
