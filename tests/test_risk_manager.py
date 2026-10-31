import importlib
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import quant_engine
from quant_engine import DataLayer, QuantConfig, RiskEngine, load_meta_model
from risk_manager import RiskManager


@pytest.fixture
def risk_engine():
    config = QuantConfig(
        api_key="",
        secret_key="",
        discord_webhook="",
        max_risk_per_trade=0.01,
        max_position_pct=0.05,
        max_daily_drawdown=0.03,
    )
    return RiskEngine(account_equity=100000.0, config=config)


def test_import_quant_engine_without_credentials_or_network(monkeypatch):
    """Verify importing quant_engine without credentials makes zero network/broker calls."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    with patch("alpaca.trading.client.TradingClient") as mock_trading_client, \
         patch("requests.post") as mock_post:
        reloaded = importlib.reload(quant_engine)
        cfg = reloaded.QuantConfig(api_key="", secret_key="", discord_webhook="")
        engine = reloaded.RiskEngine(account_equity=100000.0, config=cfg)
        assert engine.size_position(100.0, 50.0) == 20
        mock_trading_client.assert_not_called()
        mock_post.assert_not_called()


def test_import_and_use_quant_engine_without_ml_model(tmp_path):
    """Verify missing ML model file is handled cleanly and never blocks RiskEngine."""
    missing_model = tmp_path / "nonexistent_model.pkl"
    model = load_meta_model(model_path=missing_model, force_reload=True)
    assert model is None


def test_import_standalone_modules_without_external_services(monkeypatch):
    """Verify risk_manager, data_fetcher, and stats_analyzer import with zero network calls."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    with patch("alpaca.data.historical.StockHistoricalDataClient") as mock_hist, \
         patch("alpaca.trading.client.TradingClient") as mock_trade, \
         patch("requests.post") as mock_post:
        import risk_manager
        import data_fetcher
        import stats_analyzer

        importlib.reload(risk_manager)
        importlib.reload(data_fetcher)
        importlib.reload(stats_analyzer)

        mock_hist.assert_not_called()
        mock_trade.assert_not_called()
        mock_post.assert_not_called()


def test_daily_circuit_breaker(risk_engine):
    # Start of day equity $105,000 -> Current $100,000 (4.76% drawdown > 3% limit)
    assert risk_engine.check_daily_circuit_breaker(start_of_day_equity=105000.0) is False
    # Start of day equity $101,000 -> Current $100,000 (0.99% drawdown < 3% limit)
    assert risk_engine.check_daily_circuit_breaker(start_of_day_equity=101000.0) is True


def test_position_sizing_by_1_percent_risk_limit(risk_engine):
    # Account: $100k. 1% Risk = $1,000 max loss. 5% Notional = $5,000 max.
    # Entry: $100, Stop Loss: $50 -> Risk per share = $50 -> 20 shares ($2,000 notional <= $5,000 cap).
    shares = risk_engine.size_position(entry_price=100.0, stop_loss_price=50.0)
    assert shares == 20


def test_position_sizing_by_5_percent_notional_concentration(risk_engine):
    # Account: $100k. 1% Risk = $1,000. 5% Max Position = $5,000 notional max.
    # Entry: $100, Stop Loss: $90 -> By 1% risk = 100 shares ($10,000 notional).
    # Clamped by 5% concentration cap -> $5,000 / $100 = 50 shares.
    shares = risk_engine.size_position(entry_price=100.0, stop_loss_price=90.0)
    assert shares == 50

    # Tight stop ($1 risk/share): 1,000 shares by risk, clamped to 50 shares by 5% notional cap.
    shares_tight = risk_engine.size_position(entry_price=100.0, stop_loss_price=99.0)
    assert shares_tight == 50


def test_risk_manager_compatibility_and_concentration_cap():
    """Verify standalone RiskManager matches RiskEngine on both 1% risk and 5% notional caps."""
    rm = RiskManager(account_balance=100000.0, max_risk_per_trade=0.01, max_position_pct=0.05)
    re_engine = RiskEngine(
        account_equity=100000.0,
        config=QuantConfig(max_risk_per_trade=0.01, max_position_pct=0.05),
    )

    # Case 1: 1% risk limit binds (20 shares)
    assert rm.calculate_position_size(100.0, 50.0) == 20
    assert rm.calculate_position_size(100.0, 50.0) == re_engine.size_position(100.0, 50.0)

    # Case 2: 5% concentration cap binds (50 shares instead of 1,000 shares)
    assert rm.calculate_position_size(100.0, 99.0) == 50
    assert rm.calculate_position_size(100.0, 99.0) == re_engine.size_position(100.0, 99.0)

    # Case 3: $400k account with $150 entry and $145 stop:
    # 1% risk = $4,000 / $5 = 800 shares ($120,000 notional = 30% of account!)
    # 5% notional cap = $20,000 / $150 = 133 shares
    rm_400k = RiskManager(account_balance=400000.0)
    assert rm_400k.calculate_position_size(150.0, 145.0) == 133


def test_zero_and_invalid_stop_loss(risk_engine):
    assert risk_engine.size_position(entry_price=100.0, stop_loss_price=100.0) == 0
    rm = RiskManager(account_balance=100000.0)
    assert rm.calculate_position_size(entry_price=100.0, stop_loss_price=100.0) == 0

    with pytest.raises(ValueError):
        risk_engine.size_position(entry_price=-10.0, stop_loss_price=90.0)
    with pytest.raises(ValueError):
        rm.calculate_position_size(entry_price=0.0, stop_loss_price=90.0)


def test_cache_filename_generation_cross_platform():
    """Master cache names are deterministic, sanitized and independent of lookback days."""
    from alpaca.data.timeframe import TimeFrame

    filename = DataLayer.build_cache_filename(symbol="SPY", timeframe=TimeFrame.Day)
    assert filename == "SPY_1Day_master.parquet"

    # Different requested history horizons must share the same incremental cache.
    assert DataLayer.get_cache_path("SPY", TimeFrame.Day).name == filename

    dirty_filename = DataLayer.build_cache_filename(symbol=" spy* ", timeframe=" *1Day* ")
    assert dirty_filename == filename

    forbidden_chars = set('<>:"/\\\\|?* ')
    assert not (forbidden_chars & set(filename))
    assert not (forbidden_chars & set(dirty_filename))
