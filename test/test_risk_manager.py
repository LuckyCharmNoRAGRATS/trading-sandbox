import pytest
from quant_engine import RiskEngine, QuantConfig

@pytest.fixture
def risk_engine():
    config = QuantConfig(max_risk_per_trade=0.01, max_position_pct=0.05, max_daily_drawdown=0.03)
    # Simulate a $100,000 account
    return RiskEngine(account_equity=100000.0, config=config)

def test_daily_circuit_breaker(risk_engine):
    # Start of day equity was $105,000. Current is $100,000. Drawdown is 4.7% (Exceeds 3% limit)
    assert risk_engine.check_daily_circuit_breaker(start_of_day_equity=105000.0) == False

    # Start of day equity was $101,000. Current is $100,000. Drawdown is ~1% (Passes)
    assert risk_engine.check_daily_circuit_breaker(start_of_day_equity=101000.0) == True

def test_position_sizing_by_risk(risk_engine):
    # Account: $100k. 1% Risk = $1,000 max loss.
    # Entry: $100, Stop Loss: $90. Risk per share = $10.
    # Expected shares = 1000 / 10 = 100 shares.
    shares = risk_engine.size_position(entry_price=100.0, stop_loss_price=90.0)
    assert shares == 100

def test_position_sizing_by_notional_concentration(risk_engine):
    # Account: $100k. 5% Max Position = $5,000 notional max.
    # Entry: $100, Stop Loss: $99. Risk per share = $1.
    # By risk: $1,000 / $1 = 1,000 shares ($100k notional - too high!)
    # By concentration cap: $5,000 / $100 = 50 shares limit.
    shares = risk_engine.size_position(entry_price=100.0, stop_loss_price=99.0)
    assert shares == 50

def test_zero_distance_stop_loss(risk_engine):
    # Stop loss equals entry price (should return 0 to prevent division by zero errors)
    shares = risk_engine.size_position(entry_price=100.0, stop_loss_price=100.0)
    assert shares == 0