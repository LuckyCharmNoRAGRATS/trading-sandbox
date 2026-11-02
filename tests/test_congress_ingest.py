"""Offline contract tests: no HTTP sessions, Supabase writes or brokerage calls."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import congress_ingest
from congress_ingest import normalize_trade, fetch_trades, ingest, SourceError

SAMPLE = {"member": "Nancy Pelosi", "chamber": "House", "ticker": "NVDA",
          "trade_type": "buy", "amount": "$1,001 - $15,000",
          "tx_date": "2026-03-19", "disclosed": "2026-04-14",
          "asset": "NVIDIA Corporation - Common Stock", "link": "https://example.com/ptr"}


class FakeTable:
    def __init__(self):
        self.records = []
        self.calls = 0
    def upsert(self, records, **kwargs):
        assert kwargs == {"on_conflict": "event_id", "ignore_duplicates": True}
        self.records.extend(records)
        self.calls += 1
        return self
    def execute(self):
        return self


class FakeDB:
    def __init__(self):
        self.t = FakeTable()
    def table(self, name):
        assert name == "politician_disclosures"
        return self.t


def test_normalizes_disclosure_date_and_deterministic_hash():
    one = normalize_trade(SAMPLE)
    assert one["trade_type"] == "buy"
    assert one["filing_date"] == "2026-04-14"
    assert one["transaction_date"] == "2026-03-19"
    assert one["event_id"] == normalize_trade(dict(SAMPLE))["event_id"]
    assert "first_seen_at" not in one  # DB assigns first observation time.


def test_rejects_missing_disclosure_date():
    with pytest.raises(ValueError):
        normalize_trade({**SAMPLE, "disclosed": ""})


def test_rejects_future_transaction_or_filing():
    with pytest.raises(ValueError, match="future"):
        normalize_trade({**SAMPLE, "tx_date": "2099-01-01", "disclosed": "2099-02-01"})


def test_rejects_out_of_order_dates():
    with pytest.raises(ValueError, match="predates"):
        normalize_trade({**SAMPLE, "disclosed": "2026-03-01"})


def test_same_row_exactly_once_in_one_mock_sql_statement():
    db = FakeDB()
    result = ingest([SAMPLE, SAMPLE], db)
    assert result == {"fetched": 2, "accepted": 1, "invalid": 0}
    assert len(db.t.records) == 1
    assert db.t.calls == 1


def test_member_filter_matching_without_order_execution():
    db = FakeDB()
    result = ingest([SAMPLE, {**SAMPLE, "member": "Someone Else"}], db,
                    member_filter="pelosi")
    assert result["accepted"] == 1


def test_invalid_records_prevent_all_upserts():
    db = FakeDB()
    with pytest.raises(SourceError, match="invalid disclosure"):
        ingest([SAMPLE, {**SAMPLE, "disclosed": ""}], db)
    assert db.t.calls == 0


def test_empty_batch_fails_closed():
    db = FakeDB()
    with pytest.raises(SourceError, match="Empty"):
        ingest([], db)
    assert db.t.calls == 0


def test_all_filtered_out_does_not_count_as_success():
    db = FakeDB()
    with pytest.raises(SourceError, match="No matching"):
        ingest([SAMPLE], db, member_filter="not-nancy")
    assert db.t.calls == 0


def test_conflicting_same_source_id_blocks_all_writes():
    db = FakeDB()
    a = {**SAMPLE, "id": "same-provider-id", "amount": "$1,001 - $15,000"}
    b = {**a, "amount": "$500,001 - $1,000,000"}
    with pytest.raises(SourceError, match="Conflicting"):
        ingest([a, b], db)
    assert db.t.calls == 0


def test_oversized_batch_blocks_partial_writes():
    db = FakeDB()
    rows = [{**SAMPLE, "id": str(i)} for i in range(101)]
    with pytest.raises(SourceError, match="single-transaction"):
        ingest(rows, db)
    assert db.t.calls == 0


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def test_alternative_provider_requires_source_freshness_and_untruncated_page():
    source = FakeSession({"trades": [SAMPLE],
                          "last_updated": datetime.now(timezone.utc).isoformat()})
    assert fetch_trades(source, days=30, limit=10) == [SAMPLE]
    assert source.calls[0][1]["params"] == {"limit": 10, "days": 30}


def test_alternative_full_page_is_not_assumed_complete():
    source = FakeSession({"trades": [SAMPLE] * 2,
                          "last_updated": datetime.now(timezone.utc).isoformat()})
    with pytest.raises(SourceError, match="completeness"):
        fetch_trades(source, days=30, limit=2)


def test_alternative_missing_timestamp_blocks():
    with pytest.raises(SourceError, match="freshness"):
        fetch_trades(FakeSession({"trades": [SAMPLE]}), days=30, limit=10)


def test_bad_alternative_response_blocks():
    with pytest.raises(SourceError, match="trades array"):
        fetch_trades(FakeSession({"foo": []}), days=30, limit=10)


def test_cli_default_no_fetch_or_db(monkeypatch):
    monkeypatch.setattr(congress_ingest, "session_with_retries",
                        lambda: (_ for _ in ()).throw(AssertionError("HTTP was accessed")))
    with pytest.raises(SystemExit) as exc:
        congress_ingest.main([])
    assert exc.value.code == 2


def test_cli_disallows_write_without_approval_before_network(monkeypatch):
    monkeypatch.delenv("CONGRESS_SYNC_APPROVED", raising=False)
    monkeypatch.setattr(congress_ingest, "session_with_retries",
                        lambda: (_ for _ in ()).throw(AssertionError("HTTP was accessed")))
    with pytest.raises(SystemExit) as exc:
        congress_ingest.main(["--allow-network-fetch"])
    assert exc.value.code == 2


def test_cli_synthetic_fixture_path_never_uses_network_or_db(monkeypatch, capsys):
    monkeypatch.setattr(congress_ingest, "session_with_retries",
                        lambda: (_ for _ in ()).throw(AssertionError("HTTP was accessed")))
    fixture = Path(__file__).parent / "fixtures/tracefour_pelosi_synthetic.json"
    assert congress_ingest.main(["--offline-fixture", str(fixture)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"mode": "synthetic_fixture_only", "validated": 1,
                      "live_data_verified": False, "database_writes": False}


def test_cli_refuses_fixture_combined_with_network_permission():
    fixture = Path(__file__).parent / "fixtures/tracefour_pelosi_synthetic.json"
    with pytest.raises(SystemExit) as exc:
        congress_ingest.main(["--offline-fixture", str(fixture),
                              "--allow-network-fetch"])
    assert exc.value.code == 2


def test_no_broker_execution_exported():
    assert not hasattr(congress_ingest, "submit_order")
