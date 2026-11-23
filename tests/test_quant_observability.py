"""Synthetic-only tests: no broker, network, database, or deployment access."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D
import pytest
from quant_observability import (Meta, OrderEvent, FillEvent, PositionSnapshot,
    CashMovement, FeeEvent, AccountSnapshot, DataFreshness, SystemHealth,
    reconcile, report, freshness, audit_metadata, PnLSnapshot, DrawdownSnapshot)

T = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)

def m(name="evt"): return Meta(name, T, "synthetic")
def fill(name="f1", price="100"): return FillEvent(m(name), name, "o1", "SPY", "buy", D("2"), D(price), D("0.2"))
def pos(q="2", pnl="3"): return PositionSnapshot(m("p1"), "SPY", D(q), D("203"), D(pnl) if pnl is not None else None)
def acct(cash="800", equity="1003", realized="12"):
    return AccountSnapshot(m("a1"), D(equity), D(cash), D(realized))
def recon(fs=None, bs=None, ps=None, bps=None, a=None, ba=None, complete=True):
    return reconcile(fs if fs is not None else [fill()], bs if bs is not None else [fill()],
        ps if ps is not None else [pos()], bps if bps is not None else [pos()],
        a if a is not None else acct(), ba if ba is not None else acct(),
        broker_snapshot_complete=complete)

def test_reconciles_exact_independent_snapshots():
    r = recon(); assert r.verified and r.matched_fills == 1 and not r.issues

def test_missing_fill_fails_closed():
    r = recon(bs=[]); assert not r.verified and "missing_broker_fill:f1" in r.issues

def test_conflicting_fill_price_fails_closed():
    r = recon(bs=[fill(price="101")]); assert "fill_mismatch:f1" in r.issues

def test_duplicate_broker_fill_fails_closed():
    r = recon(bs=[fill(), fill()]); assert "duplicate_broker_fill:f1" in r.issues

def test_incomplete_snapshot_never_verifies():
    r = recon(complete=False); assert not r.verified

def test_missing_positions_and_account_mismatch():
    r = reconcile([fill()], [fill()], [pos()], [], acct(), acct(cash="799"), broker_snapshot_complete=True)
    assert "missing_broker_position:SPY" in r.issues and "account_mismatch" in r.issues

def test_no_fills_can_reconcile_with_explicit_complete_attestation():
    r = reconcile([], [], [], [], acct(), acct(), broker_snapshot_complete=True)
    assert r.verified and r.matched_fills == 0

def test_report_fees_pnl_drawdown_and_cash_flow():
    fee = FeeEvent(m("fee"), "fee1", "commission", D("2.50"))
    cash = CashMovement(m("dep"), "cash1", "deposit", D("100"))
    rpt = report(acct(), [pos()], [fee], [cash], recon(), peak_equity=D("1100"), fees_complete=True)
    assert rpt.status == "RECONCILED" and rpt.unrealized_pnl == D("3")
    assert rpt.net_realized_pnl == D("9.50") and rpt.net_external_cash_flow == D("100")
    assert rpt.raw_equity_drawdown_pct == (D("97")/D("1100")*D("100"))

def test_unverified_report_does_not_claim_net_realized():
    rpt = report(acct(), [pos()], [], [], recon(complete=False))
    assert rpt.status == "UNVERIFIED" and rpt.net_realized_pnl is None

def test_unknown_position_pnl_propagates():
    rpt = report(acct(), [pos(pnl=None)], [], [], recon(complete=False))
    assert rpt.unrealized_pnl is None

def test_mixed_currency_rejected():
    with pytest.raises(ValueError, match="mixed currencies"):
        report(acct(), [PositionSnapshot(m("p"), "SPY", D(1), D(100), D(0), "EUR")], [], [], recon())

def test_float_nan_and_naive_timestamp_rejected():
    with pytest.raises(ValueError): Meta("e", datetime(2026, 1, 1), "synthetic")
    with pytest.raises(ValueError): FillEvent(m(), "f", "o", "SPY", "buy", 1.0, D(1), D(0))
    with pytest.raises(ValueError): FillEvent(m(), "f", "o", "SPY", "buy", D("NaN"), D(1), D(0))

def test_freshness_and_clock_skew():
    obs = DataFreshness(m(), "iex", T-timedelta(minutes=20), 300)
    assert freshness(obs, now=T) == "STALE"
    assert freshness(DataFreshness(m(), "iex", None, 300), now=T) == "UNKNOWN"
    assert freshness(DataFreshness(m(), "iex", T+timedelta(seconds=1), 300), now=T) == "CLOCK_SKEW"

def test_secrets_not_present_in_allowlisted_audit_metadata():
    secret = "ALPACA_SECRET_KEY=NEVER-LOG-ME"
    assert secret not in str(audit_metadata(m(), "fill", "observed"))
    with pytest.raises(ValueError): SystemHealth(m(), "engine", "down", secret)

def test_order_overfill_rejected():
    with pytest.raises(ValueError): OrderEvent(m(), "o1", "SPY", "buy", "filled", D(1), D(2))


def test_unconfirmed_fees_remain_unavailable():
    rpt = report(acct(), [pos()], [], [], recon())
    assert rpt.net_realized_pnl is None

def test_decimal_string_is_normalized():
    f = FillEvent(m(), "f", "o", "SPY", "buy", "1.25", "125.00", "0.1")
    assert isinstance(f.quantity, D) and f.quantity == D("1.25")


def test_drawdown_schema_and_pnl_schema():
    assert DrawdownSnapshot(m(), D("100"), D("90")).raw_drawdown_pct == D("10")
    snap = PnLSnapshot(m(), D("2"), None, D("0.1"))
    assert snap.unrealized is None

def test_audit_does_not_emit_arbitrary_source_or_raw_identifier():
    metadata = audit_metadata(Meta("sensitive-identifier", T, "some_unknown_source"), "reconcile", "ok")
    assert metadata["source"] == "other" and "sensitive-identifier" not in str(metadata)
