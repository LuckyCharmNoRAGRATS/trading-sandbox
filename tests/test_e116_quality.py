"""Offline, synthetic Tracefour envelope and sync-monitoring tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from congress_quality import (FeedIntegrityError, TRACEFOUR_MAX_TRADES,
                              validate_tracefour_feed, assess_sync_health)

NOW = datetime(2026, 9, 9, 19, 0, tzinfo=timezone.utc)


def sample_trade():
    return {
        "filingId": "nancy-pelosi|UBER|2026-05-29|Purchase|test|Spouse",
        "memberSlug": "nancy-pelosi", "memberName": "Nancy Pelosi",
        "ticker": "UBER", "assetType": "Stock Option", "type": "Purchase",
        "owner": "Spouse", "transactionDate": "2026-05-29",
        "disclosureDate": "2026-06-24", "sourceLink":
        "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034836.pdf",
    }


def envelope(trades=None):
    return {
        "data": {"member": {"slug": "nancy-pelosi"},
                 "trades": [sample_trade()] if trades is None else trades},
        "error": None,
        "meta": {"count": 1, "fetchedAt": "2026-09-09T18:33:18.628Z",
                 "source": "sqlite"},
    }


def test_documented_member_payload_and_meta_count_semantics():
    # meta.count refers to ONE member; it does not have to equal len(trades).
    data = envelope([sample_trade(), {**sample_trade(), "filingId": "second"}])
    result = validate_tracefour_feed(data, now=NOW)
    assert result["record_count"] == 2
    assert result["cache_fetched_at"] == "2026-09-09T18:33:18.628000+00:00"
    assert result["complete_within_endpoint_cap"] is True
    assert result["trading_authorized"] is False


@pytest.mark.parametrize("change,match", [
    ({"data": {}}, "identity"),
    ({"error": {"message": "bad"}}, "error"),
    ({"meta": None}, "envelope"),
])
def test_invalid_envelopes_fail_closed(change, match):
    data = envelope()
    data.update(change)
    with pytest.raises(FeedIntegrityError, match=match):
        validate_tracefour_feed(data, now=NOW)


def test_rejects_stale_cache_even_when_trade_has_old_ingested_at():
    data = envelope()
    data["meta"]["fetchedAt"] = "2026-09-08T18:00:00Z"
    with pytest.raises(FeedIntegrityError, match="stale"):
        validate_tracefour_feed(data, now=NOW)


def test_rejects_future_source_timestamp():
    data = envelope()
    data["meta"]["fetchedAt"] = "2026-09-09T19:15:00Z"
    with pytest.raises(FeedIntegrityError, match="future"):
        validate_tracefour_feed(data, now=NOW)


def test_requires_cache_timestamp_with_timezone():
    data = envelope()
    data["meta"]["fetchedAt"] = "2026-09-09T19:00:00"
    with pytest.raises(FeedIntegrityError, match="timezone-aware"):
        validate_tracefour_feed(data, now=NOW)


def test_rejects_500_record_boundary():
    with pytest.raises(FeedIntegrityError, match="500-record"):
        validate_tracefour_feed(envelope([sample_trade()] * TRACEFOUR_MAX_TRADES), now=NOW)


def test_empty_feed_cannot_be_misreported_as_success():
    with pytest.raises(FeedIntegrityError, match="Empty"):
        validate_tracefour_feed(envelope([]), now=NOW)


@pytest.mark.parametrize("field,value,error", [
    ("filingId", "", "filing identifier"),
    ("memberSlug", "not-pelosi", "different member"),
    ("memberName", "Someone Else", "member name"),
    ("sourceLink", "http://example.com/ptr", "HTTPS"),
])
def test_invalid_filing_provenance_blocks(field, value, error):
    row = sample_trade()
    row[field] = value
    with pytest.raises(FeedIntegrityError, match=error):
        validate_tracefour_feed(envelope([row]), now=NOW)


def test_sync_health_does_not_claim_ready_for_zero_runs():
    result = assess_sync_health([], now=NOW)
    assert result["ready"] is False
    assert result["reason"] == "no_successful_sync"


def test_recent_success_reports_ready_with_real_counts():
    result = assess_sync_health([
        {"status": "success", "finished_at": "2026-09-09T18:00:00Z",
         "fetched": 23, "accepted": 22}
    ], now=NOW)
    assert result["ready"] is True
    assert result["last_accepted"] == 22


def test_old_success_is_stale():
    result = assess_sync_health([
        {"status": "success", "finished_at": "2026-09-09T04:00:00Z",
         "fetched": 23, "accepted": 23}
    ], now=NOW)
    assert result["ready"] is False and result["reason"] == "sync_stale"


def test_success_with_zero_records_does_not_qualify():
    result = assess_sync_health([
        {"status": "success", "finished_at": "2026-09-09T18:00:00Z",
         "fetched": 0, "accepted": 0}
    ], now=NOW)
    assert result["ready"] is False
    assert result["reason"] == "unverified_sync_counts"


def test_future_success_timestamp_is_error():
    with pytest.raises(FeedIntegrityError, match="future"):
        assess_sync_health([{"status": "success",
            "finished_at": "2026-09-09T20:00:00Z", "fetched": 1, "accepted": 1}], now=NOW)
