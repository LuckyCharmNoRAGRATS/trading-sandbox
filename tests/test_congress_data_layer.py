from datetime import datetime, timezone
import pytest
from congress_data_layer import filings_asof, disclosure_candidates

class Query:
    def __init__(self, rows):
        self.data = rows
        self.calls = []
    def select(self, columns):
        self.calls.append(("select", columns))
        return self
    def ilike(self, field, value):
        self.calls.append(("ilike", field, value))
        return self
    def lte(self, field, value):
        self.calls.append(("lte", field, value))
        return self
    def order(self, field, **kwargs):
        self.calls.append(("order", field))
        return self
    def limit(self, limit):
        self.calls.append(("limit", limit))
        return self
    def execute(self):
        return self

class DB:
    def __init__(self, rows):
        self.query = Query(rows)
    def table(self, name):
        assert name == "politician_disclosures"
        return self.query


NOW = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)


def test_rejects_naive_asof():
    with pytest.raises(ValueError):
        filings_asof(DB([]), asof=datetime(2026, 11, 2))


def test_enforces_first_seen_filter():
    db = DB([{"ticker": "NVDA", "trade_type": "buy"}])
    rows = filings_asof(db, asof=NOW)
    assert len(rows) == 1
    assert ("lte", "first_seen_at", NOW.isoformat()) in db.query.calls
    assert ("ilike", "member_name", "%Nancy Pelosi%") in db.query.calls


def test_candidates_exclude_non_equities_and_unrecognized_actions():
    db = DB([
        {"ticker": "NVDA", "trade_type": "buy", "asset_type": "Stock"},
        {"ticker": "UBER", "trade_type": "buy", "asset_type": "Stock Option"},
        {"ticker": None, "trade_type": "buy", "asset_type": "Stock"},
        {"ticker": "TSLA", "trade_type": "other", "asset_type": "Stock"},
    ])
    assert disclosure_candidates(db, asof=NOW) == [{"ticker": "NVDA", "trade_type": "buy", "asset_type": "Stock"}]


def test_bounded_read():
    with pytest.raises(ValueError):
        filings_asof(DB([]), asof=NOW, limit=1001)
