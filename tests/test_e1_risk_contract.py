"""E1 risk validation regressions; calculations run locally only."""
import ast
from pathlib import Path
import math

import pytest

from quant_engine import QuantConfig, RiskEngine

ROOT = Path(__file__).resolve().parent.parent


def test_single_canonical_quant_config_definition():
    tree = ast.parse((ROOT / "quant_engine.py").read_text(encoding="utf-8"))
    definitions = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "QuantConfig"]
    assert len(definitions) == 1, "Duplicate QuantConfig definitions create divergent risk contracts"


def test_portfolio_exposure_caps_proposed_order():
    cfg = QuantConfig(max_risk_per_trade=0.01, max_position_pct=0.05, max_daily_drawdown=0.03, max_portfolio_exposure_pct=0.50)
    risk = RiskEngine(account_equity=100000, config=cfg)
    quantity = risk.validate_and_size_order(
        symbol="SPY", entry_price=100, stop_loss_price=90,
        open_positions=[], pending_orders=[], current_portfolio_exposure=49000,
        start_of_day_equity=100000,
    )
    assert quantity == 10


@pytest.mark.parametrize("entry,stop", [
    (float("nan"), 90), (100, float("nan")), (float("inf"), 90),
    (100, float("-inf")), (0, 90), (-1, 90),
])
def test_nonfinite_and_invalid_prices_are_rejected(entry, stop):
    risk = RiskEngine(account_equity=100000, config=QuantConfig())
    with pytest.raises(ValueError):
        risk.size_position(entry, stop)


def test_daily_drawdown_blocks_new_positions():
    risk = RiskEngine(account_equity=96000, config=QuantConfig(max_daily_drawdown=0.03))
    assert risk.validate_and_size_order(
        symbol="SPY", entry_price=100, stop_loss_price=95,
        open_positions=[], pending_orders=[], current_portfolio_exposure=0,
        start_of_day_equity=100000
    ) == 0


def test_existing_and_pending_positions_block_duplicate_entries():
    risk = RiskEngine(account_equity=100000, config=QuantConfig())
    base = dict(symbol="SPY", entry_price=100, stop_loss_price=95,
                current_portfolio_exposure=0, start_of_day_equity=100000)
    assert risk.validate_and_size_order(**base, open_positions=["SPY"], pending_orders=[]) == 0
    assert risk.validate_and_size_order(**base, open_positions=[], pending_orders=["SPY"]) == 0
