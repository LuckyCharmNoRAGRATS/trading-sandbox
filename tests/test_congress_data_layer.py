"""No-network E1.15 as-of research integration contract."""
from datetime import datetime, timezone
import pytest

from congress_data_layer import filings_asof, disclosure_candidates
from congress_research_guard import DisclosureIntegrityError

NOW = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)


def row(event="a" * 64, **overrides):
    item = {
        "event_id": event, "provider": "tracefour",
        "member_name": "Nancy Pelosi", "chamber": "house",
        "ticker": "NVDA", "trade_type": "buy", "asset_type": "Stock",
        "owner_type": "Spouse", "transaction_date": "2026-03-19",
        "filing_date": "2026-04-14", "first_seen_at": "2026-11-02T09:00:00+00:00",
        "filing_url": "https://example.invalid/filing.pdf",
    }
    item.update(overrides)
    return item


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
        self.calls.append(("order", field, kwargs))
        return self
    def limit(self, n):
        self.calls.append(("limit", n))
        return self
    def execute(self):
        return self


class DB:
    def __init__(self, rows):
        self.query = Query(rows)
    def table(self, name):
        assert name == "politician_disclosures"
        return self.query


def test_naive_asof_fails_closed():
    with pytest.raises(DisclosureIntegrityError, match="timezone-aware"):
        filings_asof(DB([]), asof=datetime(2026, 11, 2))


def test_query_first_seen_filter_limit_plus_one():
    db = DB([row()])
    assert filings_asof(db, asof=NOW, limit=1) == [row()]
    assert ("lte", "first_seen_at", NOW.isoformat()) in db.query.calls
    assert ("ilike", "member_name", "%Nancy Pelosi%") in db.query.calls
    assert ("order", "first_seen_at", {"desc": True}) in db.query.calls
    assert ("limit", 2) in db.query.calls


def test_cannot_silently_truncate_backtests():
    with pytest.raises(DisclosureIntegrityError, match="pagination required"):
        filings_asof(DB([row(), row("b" * 64)]), asof=NOW, limit=1)


def test_candidates_exclude_options_and_unknown_assets():
    source = DB([
        row(),
        row("b" * 64, ticker="UBER", asset_type="Stock Option"),
        row("c" * 64, ticker="TSLA", asset_type=""),
        row("d" * 64, ticker="AMZN", trade_type="other"),
    ])
    selected = disclosure_candidates(source, asof=NOW)
    assert len(selected) == 1
    assert selected[0]["ticker"] == "NVDA"
    assert selected[0]["disclosed_action"] == "buy"
    assert selected[0]["owner_type"] == "Spouse"
    assert "order" not in selected[0]


def test_candidates_require_usable_provenance():
    selected = disclosure_candidates(DB([row(filing_url=None)]), asof=NOW)
    assert selected == []


def test_future_first_seen_not_actionable():
    with pytest.raises(DisclosureIntegrityError, match="Future observation"):
        disclosure_candidates(DB([row(first_seen_at="2026-11-03T12:00:00Z")]), asof=NOW)


def test_wrong_member_not_mistaken_for_nancy():
    with pytest.raises(DisclosureIntegrityError, match="Wrong member"):
        disclosure_candidates(DB([row(member_name="Paul Pelosi")]), asof=NOW)


def test_limit_outside_valid_range_fails_closed():
    with pytest.raises(ValueError, match="limit"):
        filings_asof(DB([]), asof=NOW, limit=1001)
