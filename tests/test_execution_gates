import pytest
from quant_engine import RiskEngine, QuantConfig

@pytest.fixture
def risk_engine():
    # $100k account, max 50% deployed ($50,000 max portfolio exposure)
    config = QuantConfig(max_portfolio_exposure_pct=0.50, max_position_pct=0.05, max_risk_per_trade=0.01)
    return RiskEngine(account_equity=100000.0, config=config)

def test_duplicate_order_rejection(risk_engine):
    # Should reject if already in open positions
    shares = risk_engine.validate_and_size_order(
        symbol="AAPL", entry_price=150.0, stop_loss_price=140.0,
        open_positions=["AAPL", "MSFT"], pending_orders=[],
        current_portfolio_exposure=10000.0, start_of_day_equity=100000.0
    )
    assert shares == 0

    # Should reject if a pending order exists
    shares_pending = risk_engine.validate_and_size_order(
        symbol="TSLA", entry_price=200.0, stop_loss_price=190.0,
        open_positions=["MSFT"], pending_orders=["TSLA"],
        current_portfolio_exposure=10000.0, start_of_day_equity=100000.0
    )
    assert shares_pending == 0

def test_portfolio_exposure_limit(risk_engine):
    # Already using $50,000 of our $50,000 limit (50% of $100k)
    shares = risk_engine.validate_and_size_order(
        symbol="NVDA", entry_price=100.0, stop_loss_price=90.0,
        open_positions=["MSFT", "AAPL"], pending_orders=[],
        current_portfolio_exposure=50000.0, start_of_day_equity=100000.0
    )
    assert shares == 0 # Completely rejected

def test_portfolio_exposure_scale_down(risk_engine):
    # We are at $48,000 exposure. Limit is $50,000. We have $2,000 capacity left.
    # Normal 5% position limit would allow $5,000 (50 shares at $100).
    # It must scale down to fit the $2,000 remaining capacity (20 shares).
    shares = risk_engine.validate_and_size_order(
        symbol="NVDA", entry_price=100.0, stop_loss_price=90.0,
        open_positions=["MSFT"], pending_orders=[],
        current_portfolio_exposure=48000.0, start_of_day_equity=100000.0
    )
    assert shares == 20