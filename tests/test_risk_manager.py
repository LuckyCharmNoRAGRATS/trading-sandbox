import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from quant_engine import RiskEngine, QuantConfig


def test_1_percent_risk_vs_5_percent_notional_cap():
    cfg = QuantConfig(max_risk_per_trade=0.01, max_position_pct=0.05)
    engine = RiskEngine(account_equity=400000.0, config=cfg)

    # 1% risk = $4,000 ($10 stop distance -> 400 shares)
    # 5% notional cap = $20,000 ($100 entry -> 200 shares max)
    # Institutional engine must pick the stricter cap (200 shares)
    shares = engine.size_position(entry_price=100.0, stop_loss_price=90.0)
    assert shares == 200


def test_ai_veto_blocks_low_confidence_trade():
    cfg = QuantConfig(ai_confidence_threshold=0.60)
    engine = RiskEngine(account_equity=400000.0, config=cfg)
    shares = engine.size_position(entry_price=100.0, stop_loss_price=95.0, ai_confidence=0.45)
    assert shares == 0


def test_daily_drawdown_circuit_breaker():
    cfg = QuantConfig(max_daily_drawdown=0.03)
    # Equity dropped from $400,000 to $385,000 (-3.75% drawdown)
    engine = RiskEngine(account_equity=385000.0, config=cfg)
    assert engine.check_daily_circuit_breaker(start_of_day_equity=400000.0) is False