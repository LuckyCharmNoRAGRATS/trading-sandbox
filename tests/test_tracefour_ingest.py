"""Documented Tracefour API/schema contract with fully injected fake HTTP."""
from datetime import datetime, timezone
import pytest

from congress_ingest import normalize_trade, fetch_tracefour, SourceError

NOW = datetime(2026, 9, 9, 19, tzinfo=timezone.utc)

EXAMPLE = {
    "filingId": "nancy-pelosi|UBER|2026-05-29|Purchase|$500,001 - $1,000,000|Spouse",
    "memberSlug": "nancy-pelosi", "memberName": "Nancy Pelosi",
    "chamber": "house", "ticker": "UBER",
    "assetDescription": "Uber Technologies Inc", "assetType": "Stock Option",
    "type": "Purchase", "owner": "Spouse", "transactionDate": "2026-05-29",
    "disclosureDate": "2026-06-24", "amountLabel": "$500,001 - $1,000,000",
    "sourceLink": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034836.pdf",
    "ingestedAt": "2026-06-24T18:58:56.037Z",
}


def envelope(trades=None):
    return {"data": {"member": {"slug": "nancy-pelosi", "memberName": "Nancy Pelosi"},
                     "trades": [EXAMPLE] if trades is None else trades},
            "meta": {"count": 1, "fetchedAt": "2026-09-09T18:33:18.628Z",
                     "source": "sqlite", "license": "https://creativecommons.org/licenses/by/4.0/"}}


class Response:
    def __init__(self, data):
        self.data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self.data


class Session:
    def __init__(self, data):
        self.data = data
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.data)


def test_tracefour_preserves_option_and_spouse_provenance():
    row = normalize_trade(EXAMPLE, provider="tracefour")
    assert row["member_name"] == "Nancy Pelosi"
    assert row["trade_type"] == "buy"
    assert row["transaction_date"] == "2026-05-29"
    assert row["filing_date"] == "2026-06-24"
    assert row["asset_type"] == "Stock Option"
    assert row["owner_type"] == "Spouse"
    assert row["upstream_ingested_at"] == "2026-06-24T18:58:56.037Z"
    assert row["event_id"] == normalize_trade(dict(EXAMPLE), provider="tracefour")["event_id"]


def test_tracefour_response_uses_member_trades_not_tracker_portfolio():
    source = Session(envelope())
    assert fetch_tracefour(source, now=NOW) == [EXAMPLE]
    assert source.calls == [("https://tracefour.com/v1/congress/nancy-pelosi",
                             {"timeout": (10, 30), "headers": {"Accept": "application/json"}})]


def test_wrong_member_slug_rejected_before_sync():
    body = envelope()
    body["data"]["member"]["slug"] = "pelosi"
    with pytest.raises(SourceError, match="member identity"):
        fetch_tracefour(Session(body), now=NOW)


def test_stale_cache_rejected_before_sync():
    body = envelope()
    body["meta"]["fetchedAt"] = "2026-09-08T08:00:00Z"
    with pytest.raises(SourceError, match="stale"):
        fetch_tracefour(Session(body), now=NOW)


def test_missing_meta_rejected_before_sync():
    body = envelope()
    del body["meta"]
    with pytest.raises(SourceError, match="data/meta"):
        fetch_tracefour(Session(body), now=NOW)


def test_missing_filing_source_rejected_before_sync():
    body = envelope([{**EXAMPLE, "sourceLink": ""}])
    with pytest.raises(SourceError, match="filing link"):
        fetch_tracefour(Session(body), now=NOW)


def test_api_page_limit_rejected_before_sync():
    with pytest.raises(SourceError, match="500-record"):
        fetch_tracefour(Session(envelope([EXAMPLE] * 500)), now=NOW)


def test_empty_member_feed_rejected_before_sync():
    with pytest.raises(SourceError, match="Empty"):
        fetch_tracefour(Session(envelope([])), now=NOW)


def test_member_endpoint_override_blocked_for_other_url():
    session = Session(envelope())
    with pytest.raises(SourceError, match="documented HTTPS"):
        fetch_tracefour(session, url="https://tracefour.com/v1/trackers/pelosi", now=NOW)
    assert not session.calls
