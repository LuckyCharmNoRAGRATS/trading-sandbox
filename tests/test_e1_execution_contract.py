"""E1 worker regression harness: fake clients only; never connect to Alpaca."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

import live_paper_trader


class StopAfterOneCycle(Exception):
    pass


def execute_one_cycle(monkeypatch, commands):
    broker = MagicMock()
    broker.submit_order.return_value = SimpleNamespace(id="paper-test-order-1")
    bridge = MagicMock()
    bridge.client = object()
    bridge.get_pending_commands.return_value = commands
    dashboard = SimpleNamespace(bridge=bridge)
    monkeypatch.setattr(live_paper_trader, "dash", dashboard)
    monkeypatch.setattr(live_paper_trader, "DASHBOARD_SYNC", True)
    monkeypatch.setattr(live_paper_trader, "notify_system_status", lambda **kw: None)
    monkeypatch.setattr(live_paper_trader, "notify_error", lambda *a, **kw: None)
    monkeypatch.setattr(live_paper_trader.time, "sleep", lambda seconds: (_ for _ in ()).throw(StopAfterOneCycle()))

    engine = live_paper_trader.LiveExecutionEngine.__new__(live_paper_trader.LiveExecutionEngine)
    engine.config = live_paper_trader.QuantConfig()
    engine.trading_client = broker
    engine.watchlist = []
    engine.meta_model = None
    engine.sync_portfolio_state = lambda: dict(
        equity=100000.0, start_of_day_equity=100000.0, buying_power=100000.0,
        open_symbols=[], pending_symbols=[], portfolio_exposure=0.0
    )
    with pytest.raises(StopAfterOneCycle):
        engine.execute_loop()
    return broker, bridge


def sample_command(side="BUY", **overrides):
    args = dict(symbol="SPY", side=side, entry_price=100.0, stop_loss=95.0)
    args.update(overrides)
    return dict(id=101, cmd="submit_order", args=args, status="pending", source="web")


def test_canonical_cmd_args_reach_risk_checked_broker_request(monkeypatch):
    broker, bridge = execute_one_cycle(monkeypatch, [sample_command()])
    broker.submit_order.assert_called_once()
    bridge.mark_command_processed.assert_called_once()
    assert bridge.mark_command_processed.call_args.kwargs["status"] in ("submitted", "accepted", "completed")


@pytest.mark.parametrize("side", ["HOLD", "", "PURCHASE", None])
def test_invalid_side_fails_closed_without_order(monkeypatch, side):
    broker, bridge = execute_one_cycle(monkeypatch, [sample_command(side=side)])
    broker.submit_order.assert_not_called()
    bridge.mark_command_processed.assert_called_once()
    assert bridge.mark_command_processed.call_args.kwargs["status"] == "failed"


def test_rejected_drawdown_does_not_send_broker_order(monkeypatch):
    broker, bridge = execute_one_cycle(monkeypatch, [sample_command(stop_loss=100.0)])
    broker.submit_order.assert_not_called()
    bridge.mark_command_processed.assert_called_once()


def test_broker_submission_must_have_stable_client_order_id(monkeypatch):
    broker, _ = execute_one_cycle(monkeypatch, [sample_command()])
    broker.submit_order.assert_called_once()
    request = broker.submit_order.call_args.args[0]
    assert getattr(request, "client_order_id", None), "Retries require a stable broker-side idempotency key"


def test_two_identical_pending_requests_must_not_submit_twice(monkeypatch):
    cmd = sample_command()
    broker, _ = execute_one_cycle(monkeypatch, [cmd, dict(cmd)])
    assert broker.submit_order.call_count <= 1
