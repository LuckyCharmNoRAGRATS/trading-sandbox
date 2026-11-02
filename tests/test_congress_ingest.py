"""No-network tests for the congressional data adapter."""
import pytest
from congress_ingest import normalize_trade, fetch_trades, ingest, SourceError

SAMPLE = {"member": "Nancy Pelosi", "chamber": "House", "ticker": "NVDA",
          "trade_type": "buy", "amount": "$1,001 - $15,000",
          "tx_date": "2026-03-19", "disclosed": "2026-04-14",
          "asset": "NVIDIA Corporation - Common Stock", "link": "https://example.com/ptr"}

class FakeTable:
    def __init__(self):
        self.records = []
    def upsert(self, records, **kwargs):
        assert kwargs == {"on_conflict": "event_id", "ignore_duplicates": True}
        self.records.extend(records)
        return self
    def execute(self):
        return self

class FakeDB:
    def __init__(self):
        self.t = FakeTable()
    def table(self, name):
        assert name == "politician_disclosures"
        return self.t

def test_normalizes_with_disclosure_not_transaction_entry():
    one = normalize_trade(SAMPLE)
    assert one["trade_type"] == "buy"
    assert one["filing_date"] == "2026-04-14"
    assert one["transaction_date"] == "2026-03-19"
    assert one["event_id"] == normalize_trade(dict(SAMPLE))["event_id"]
    assert "first_seen_at" not in one

def test_rejects_missing_filing_date():
    with pytest.raises(ValueError):
        normalize_trade({**SAMPLE, "disclosed": ""})

def test_rejects_future_trades():
    with pytest.raises(ValueError):
        normalize_trade({**SAMPLE, "tx_date": "2099-01-01", "disclosed": "2099-02-01"})

def test_rejects_bad_timeline():
    with pytest.raises(ValueError):
        normalize_trade({**SAMPLE, "disclosed": "2026-03-01"})

def test_same_trade_only_written_once_per_batch():
    db = FakeDB()
    metrics = ingest([SAMPLE, SAMPLE], db)
    assert metrics == {"fetched": 2, "accepted": 1, "invalid": 0}
    assert len(db.t.records) == 1

def test_filter_member_casefold():
    db = FakeDB()
    metrics = ingest([SAMPLE, {**SAMPLE, "member": "Someone Else"}], db, member_filter="pelosi")
    assert metrics["accepted"] == 1

def test_all_invalid_fails_closed():
    with pytest.raises(SourceError):
        ingest([{**SAMPLE, "disclosed": ""}], FakeDB())

def test_bad_response_fails_closed():
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"foo": []}
    class Session:
        def get(self, *args, **kwargs):
            return Response()
    with pytest.raises(SourceError):
        fetch_trades(Session(), days=30, limit=100)

def test_trading_not_in_module():
    import congress_ingest
    assert not hasattr(congress_ingest, "submit_order")
