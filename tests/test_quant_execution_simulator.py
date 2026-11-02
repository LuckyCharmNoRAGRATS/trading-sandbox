"""Offline-only simulator contract tests, with no broker SDK imports."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from copy import deepcopy

import pytest

from quant_execution import CostModel, ExecutionSimulator, OrderIntent, Quote, SimulationError


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def intent(order="sim-1", key="key-1", side="buy", qty="10", kind="market", limit=None):
    return OrderIntent(order, key, "SPY", side, Decimal(qty), kind, limit)


def quote(second, bid="99", ask="101", bid_size="10", ask_size="10"):
    return Quote(BASE + timedelta(seconds=second), bid, ask, bid_size, ask_size)


def test_duplicate_retry_has_exactly_one_order_event_and_fill():
    sim = ExecutionSimulator("10000")
    req = intent()
    sim.submit(req, at=BASE)
    sim.submit(req, at=BASE + timedelta(seconds=1))
    assert [event["kind"] for event in sim.events()] == ["ACCEPTED"]
    sim.on_quote(quote(2))
    assert sim.ledger()["orders"]["sim-1"]["filled_quantity"] == "10"
    assert len([event for event in sim.events() if event["kind"] == "FILL"]) == 1


def test_colliding_idempotency_and_reused_order_ids_fail_closed():
    sim = ExecutionSimulator("1000")
    sim.submit(intent(), at=BASE)
    with pytest.raises(SimulationError, match="collision"):
        sim.submit(intent(order="sim-2"), at=BASE)
    with pytest.raises(SimulationError, match="reused"):
        sim.submit(intent(key="another"), at=BASE)


def test_partial_fills_limit_price_and_shared_quote_liquidity():
    sim = ExecutionSimulator("10000")
    sim.submit(intent(qty="7", kind="limit", limit="102"), at=BASE)
    sim.submit(intent(order="sim-2", key="key-2", qty="7"), at=BASE)
    assert sim.on_quote(quote(1, ask_size="10")) == 2
    assert sim.ledger()["orders"]["sim-1"]["status"] == "FILLED"
    assert sim.ledger()["orders"]["sim-2"]["filled_quantity"] == "3"
    assert sim.on_quote(quote(2, ask="103", ask_size="7")) == 1
    assert sim.ledger()["orders"]["sim-2"]["status"] == "FILLED"


def test_reject_cancel_and_invalid_transitions():
    sim = ExecutionSimulator("10000")
    sim.submit(intent(), at=BASE)
    sim.reject("sim-1", at=BASE, reason="synthetic_reject")
    with pytest.raises(SimulationError, match="Invalid terminal"):
        sim.cancel("sim-1", at=BASE)
    with pytest.raises(SimulationError, match="Invalid terminal"):
        sim.reject("sim-1", at=BASE, reason="again")
    sim.submit(intent(order="sim-2", key="key-2"), at=BASE)
    sim.on_quote(quote(1, ask_size="3"))
    sim.cancel("sim-2", at=BASE + timedelta(seconds=2))
    assert sim.ledger()["orders"]["sim-2"]["status"] == "CANCELED"
    assert sim.ledger()["orders"]["sim-2"]["filled_quantity"] == "3"
    with pytest.raises(SimulationError):
        sim.reject("sim-2", at=BASE + timedelta(seconds=3), reason="too_late")


def test_spread_slippage_commission_and_ledger_determinism():
    sim = ExecutionSimulator("2000", CostModel("10", "0.02"))
    sim.submit(intent(qty="4"), at=BASE)
    sim.on_quote(quote(1, bid="99", ask="101", ask_size="2"))
    sim.on_quote(quote(2, bid="99", ask="101", ask_size="3"))
    px = Decimal("101") * Decimal("1.001")
    assert sim.cash == Decimal("2000") - px * 4 - Decimal("0.08")
    assert sim.positions["SPY"] == 4
    assert sim.total_commission == Decimal("0.08")
    assert sim.reconcile() == sim.ledger()
    copy = ExecutionSimulator.replay(sim.initial_cash, sim.costs, sim.events())
    assert copy.ledger() == sim.ledger()


def test_limit_after_slippage_and_latency():
    sim = ExecutionSimulator("5000", CostModel("100", "0", latency_ms=1500))
    sim.submit(intent(kind="limit", limit="101"), at=BASE)
    assert sim.on_quote(quote(1, ask="100")) == 0  # still within latency
    assert sim.on_quote(quote(2, ask="101")) == 0  # 1% slippage breaches limit
    assert sim.on_quote(quote(3, ask="100")) == 1
    assert sim.ledger()["orders"]["sim-1"]["status"] == "FILLED"


def test_journal_restart_idempotency_recovery(tmp_path):
    path = tmp_path / "sim.json"
    sim = ExecutionSimulator("10000")
    req = intent(qty="5")
    sim.submit(req, at=BASE)
    sim.on_quote(quote(1, ask_size="2"))
    sim.save_journal(path)
    recovered = ExecutionSimulator.load_journal(path)
    assert recovered.ledger() == sim.ledger()
    recovered.submit(req, at=BASE + timedelta(seconds=3))
    assert len([e for e in recovered.events() if e["kind"] == "ACCEPTED"]) == 1
    recovered.on_quote(quote(4, ask_size="3"))
    assert recovered.ledger()["orders"]["sim-1"]["status"] == "FILLED"
    recovered.save_journal(path)
    assert ExecutionSimulator.load_journal(path).reconcile() == recovered.ledger()


def test_event_corruption_invalid_sequence_and_replay_transition():
    sim = ExecutionSimulator("1000")
    sim.submit(intent(qty="2"), at=BASE)
    sim.on_quote(quote(1, ask_size="1"))
    damaged = deepcopy(sim.events())
    damaged[-1]["data"]["quantity"] = "999"
    with pytest.raises(SimulationError, match="hash"):
        ExecutionSimulator.replay(sim.initial_cash, sim.costs, damaged)
    swapped = deepcopy(sim.events())
    swapped[1], swapped[2] = swapped[2], swapped[1]
    with pytest.raises(SimulationError, match="sequence"):
        ExecutionSimulator.replay(sim.initial_cash, sim.costs, swapped)


def test_fail_closed_for_nonfinite_invalid_inputs_and_old_quotes():
    with pytest.raises(SimulationError):
        intent(qty="NaN")
    with pytest.raises(SimulationError):
        Quote(BASE, "101", "100", "1", "1")
    with pytest.raises(SimulationError):
        CostModel(latency_ms=-1)
    sim = ExecutionSimulator("100")
    sim.on_quote(quote(1))
    with pytest.raises(SimulationError, match="strictly increase"):
        sim.on_quote(quote(1))
    with pytest.raises(SimulationError, match="Unknown order"):
        sim.cancel("missing", at=BASE)


def test_no_broker_or_external_service_dependency():
    import subprocess
    import sys
    from pathlib import Path
    code = ("import sys; import quant_execution; "
            "assert not {'alpaca', 'supabase', 'requests'} & set(sys.modules)")
    result = subprocess.run([sys.executable, "-S", "-c", code],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
