"""E1.15 synthetic, offline congressional disclosure research controls."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from congress_research_guard import (
    DisclosureIntegrityError, evaluate_disclosures, read_filings_asof,
    validate_tracefour_member_envelope, TRACEFOUR_MEMBER_LIMIT,
)

ASOF = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def record(**override):
    row = {
        "event_id": "a" * 64, "provider": "tracefour",
        "member_name": "Nancy Pelosi", "ticker": "NVDA",
        "trade_type": "buy", "asset_type": "Stock",
        "owner_type": "Spouse", "amount_range": "$1,001 - $15,000",
        "transaction_date": "2026-03-19",
        "filing_date": "2026-04-14",
        "first_seen_at": "2026-11-02T09:00:00Z",
        "upstream_ingested_at": "2026-04-14T09:00:00Z",
        "filing_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/sample.pdf",
    }
    row.update(override)
    return row


def test_point_in_time_equity_candidate_keeps_provenance_and_owner():
    report = evaluate_disclosures([record()], asof=ASOF)
    assert report["eligible_equity_disclosures"] == 1
    assert report["order_submission_authorized"] is False
    assert report["disclosure_is_live_trade"] is False
    candidate = report["candidates"][0]
    assert candidate["owner_type"] == "Spouse"
    assert candidate["reporting_lag_days"] == 26
    assert candidate["first_seen_at"] == "2026-11-02T09:00:00+00:00"
    assert "qty" not in candidate and "order" not in candidate


@pytest.mark.parametrize("asset", ["Stock Option", "Call Option", "", None, "Bond"])
def test_options_and_unknown_asset_classes_do_not_become_equity_candidates(asset):
    report = evaluate_disclosures([record(asset_type=asset)], asof=ASOF)
    assert report["eligible_equity_disclosures"] == 0
    assert report["excluded_non_equity_or_unverified"] == 1


@pytest.mark.parametrize("action", ["exchange", "other", "", None])
def test_non_buy_sell_not_treated_as_equity_trade(action):
    report = evaluate_disclosures([record(trade_type=action)], asof=ASOF)
    assert report["eligible_equity_disclosures"] == 0


def test_missing_link_disqualifies_candidate():
    report = evaluate_disclosures([record(filing_url=None)], asof=ASOF)
    assert report["eligible_equity_disclosures"] == 0


def test_future_first_seen_causes_fail_closed_not_lookahead():
    with pytest.raises(DisclosureIntegrityError, match="Future observation"):
        evaluate_disclosures([record()], asof=datetime(2026, 7, 1, tzinfo=timezone.utc))


def test_upstream_ingestion_time_does_not_override_our_first_seen():
    older = datetime(2026, 4, 15, tzinfo=timezone.utc)
    with pytest.raises(DisclosureIntegrityError, match="Future observation"):
        evaluate_disclosures([record()], asof=older)


def test_invalid_member_identity_blocks():
    with pytest.raises(DisclosureIntegrityError, match="Wrong member"):
        evaluate_disclosures([record(member_name="Paul Pelosi")], asof=ASOF)


@pytest.mark.parametrize("bad", ["2026-03-01", "", None])
def test_invalid_filing_chronology_blocks(bad):
    with pytest.raises(DisclosureIntegrityError, match="Invalid disclosure date|Contradictory"):
        evaluate_disclosures([record(filing_date=bad)], asof=ASOF)


def test_duplicate_ids_are_not_silently_deduplicated():
    with pytest.raises(DisclosureIntegrityError, match="repeated"):
        evaluate_disclosures([record(), record()], asof=ASOF)


def test_missing_or_invalid_event_id_blocks():
    with pytest.raises(DisclosureIntegrityError, match="event identity"):
        evaluate_disclosures([record(event_id="invalid")], asof=ASOF)


def test_empty_data_is_explicit_not_fabricated():
    report = evaluate_disclosures([], asof=ASOF)
    assert report["observed_records"] == 0
    assert report["candidates"] == []


def test_naive_asof_is_refused():
    with pytest.raises(DisclosureIntegrityError, match="timezone-aware"):
        evaluate_disclosures([], asof=datetime(2026, 11, 2))


def test_tracefour_envelope_checks_member_and_completeness():
    example = {"data": {"member": {"slug": "nancy-pelosi"}, "trades": [record()]}}
    assert validate_tracefour_member_envelope(example) == [record()]
    example["data"]["member"]["slug"] = "other"
    with pytest.raises(DisclosureIntegrityError, match="Unexpected"):
        validate_tracefour_member_envelope(example)


def test_tracefour_500_items_is_ambiguous_and_rejected():
    response = {"data": {"member": {"slug": "nancy-pelosi"},
                         "trades": [record() for _ in range(TRACEFOUR_MEMBER_LIMIT)]}}
    with pytest.raises(DisclosureIntegrityError, match="500-record"):
        validate_tracefour_member_envelope(response)


def test_tracefour_invalid_trade_items_block():
    response = {"data": {"member": {"slug": "nancy-pelosi"}, "trades": [None]}}
    with pytest.raises(DisclosureIntegrityError, match="Malformed"):
        validate_tracefour_member_envelope(response)


class FakeQuery:
    def __init__(self, rows):
        self.data = rows
        self.calls = []
    def select(self, cols):
        self.calls.append(("select", cols))
        return self
    def ilike(self, key, value):
        self.calls.append(("ilike", key, value))
        return self
    def lte(self, key, value):
        self.calls.append(("lte", key, value))
        return self
    def order(self, key, **kwargs):
        self.calls.append(("order", key, kwargs))
        return self
    def limit(self, n):
        self.calls.append(("limit", n))
        return self
    def execute(self):
        self.calls.append(("execute",))
        return self


class FakeDB:
    def __init__(self, rows):
        self.query = FakeQuery(rows)
        self.tables = []
    def table(self, name):
        self.tables.append(name)
        return self.query


def test_read_only_query_checks_first_seen_and_detects_truncation():
    db = FakeDB([record()])
    got = read_filings_asof(db, asof=ASOF, limit=2)
    assert got == [record()]
    assert db.tables == ["politician_disclosures"]
    assert ("lte", "first_seen_at", ASOF.isoformat()) in db.query.calls
    assert ("limit", 3) in db.query.calls
    assert ("order", "first_seen_at", {"desc": True}) in db.query.calls


def test_read_only_query_rejects_unhandled_pagination():
    with pytest.raises(DisclosureIntegrityError, match="pagination required"):
        read_filings_asof(FakeDB([record(), record(), record()]), asof=ASOF, limit=2)


def test_read_only_query_rejects_missing_result():
    with pytest.raises(DisclosureIntegrityError, match="incomplete"):
        read_filings_asof(FakeDB(None), asof=ASOF)


def test_read_only_query_rejects_invalid_limit():
    with pytest.raises(ValueError, match="limit"):
        read_filings_asof(FakeDB([]), asof=ASOF, limit=0)


def test_research_module_does_not_expose_execution_or_network_connectors():
    import congress_research_guard as guard
    assert not hasattr(guard, "submit_order")
    assert not hasattr(guard, "connect_from_env")
