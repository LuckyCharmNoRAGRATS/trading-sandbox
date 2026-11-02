import pytest
from congress_ingest import normalize_trade, fetch_tracefour, SourceError

EXAMPLE = {
    "filingId": "nancy-pelosi|UBER|2026-05-29|Purchase|$500,001 - $1,000,000|Spouse",
    "memberName": "Nancy Pelosi", "chamber": "house", "ticker": "UBER",
    "assetDescription": "Uber Technologies Inc", "assetType": "Stock Option",
    "type": "Purchase", "owner": "Spouse", "transactionDate": "2026-05-29",
    "disclosureDate": "2026-06-24", "amountLabel": "$500,001 - $1,000,000",
    "sourceLink": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034836.pdf",
    "ingestedAt": "2026-06-24T18:58:56.037Z",
}


def test_tracefour_maps_filing_dates_and_spouse_option_information():
    record = normalize_trade(EXAMPLE, provider="tracefour")
    assert record["member_name"] == "Nancy Pelosi"
    assert record["provider"] == "tracefour"
    assert record["ticker"] == "UBER"
    assert record["trade_type"] == "buy"
    assert record["transaction_date"] == "2026-05-29"
    assert record["filing_date"] == "2026-06-24"
    assert record["asset_type"] == "Stock Option"
    assert record["owner_type"] == "Spouse"
    assert record["upstream_ingested_at"] == "2026-06-24T18:58:56.037Z"
    assert record["event_id"] == normalize_trade(dict(EXAMPLE), provider="tracefour")["event_id"]


def test_tracefour_requires_correct_member_identity():
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": {"member": {"slug": "unknown"}, "trades": [EXAMPLE]}}
    class Session:
        def get(self, url, **kwargs):
            assert url == "https://tracefour.com/v1/congress/nancy-pelosi"
            return Response()
    with pytest.raises(SourceError):
        fetch_tracefour(Session())


def test_tracefour_parses_member_trade_envelope():
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": {"member": {"slug": "nancy-pelosi"}, "trades": [EXAMPLE]}}
    class Session:
        def get(self, *args, **kwargs):
            return Response()
    assert fetch_tracefour(Session()) == [EXAMPLE]
