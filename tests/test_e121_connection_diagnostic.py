"""Offline Tracefour transport and data-quality diagnostic contract tests."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from congress_connection_diagnostic import (
    DiagnosticError, MAX_JSON_BYTES, TRACEFOUR_URL, REQUEST_TIMEOUT,
    assess_body, diagnose_saved_json, main, probe_once,
)

NOW = datetime(2026, 11, 2, 10, 6, 40, tzinfo=timezone.utc)


def trade(**update):
    item = {
        "filingId": "nancy-pelosi|NVDA|2026-06-01|Purchase|Spouse|Stock",
        "memberSlug": "nancy-pelosi",
        "memberName": "Nancy Pelosi",
        "assetType": "Stock",
        "ticker": "NVDA",
        "type": "Purchase",
        "transactionDate": "2026-06-01",
        "disclosureDate": "2026-06-30",
        "sourceLink": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000000.pdf",
    }
    item.update(update)
    return item


def body(*rows, fetched="2026-11-02T09:59:00Z"):
    return {
        "data": {"member": {"slug": "nancy-pelosi", "memberName": "Nancy Pelosi"},
                 "trades": list(rows) if rows else [trade()]},
        "meta": {"fetchedAt": fetched, "source": "sqlite", "count": 1},
    }


class Response:
    def __init__(self, payload, status=200, headers=None, content=None, chunks=None):
        self.status_code = status
        self.headers = headers if headers is not None else {"Content-Type": "application/json"}
        self.payload = payload
        self.raw = json.dumps(payload).encode() if content is None else content
        self.chunks = chunks
        self.closed = False
    def iter_content(self, chunk_size=65536):
        if self.chunks is not None:
            yield from self.chunks
        else:
            yield self.raw
    def close(self):
        self.closed = True


class Session:
    def __init__(self, result):
        self.result = result
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def save(tmp_path, payload):
    path = tmp_path / "saved.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_valid_envelope_still_not_ingestion_ready():
    result = assess_body(body(), now=NOW)
    assert result["quality_status"] == "structurally_valid_source_unverified"
    assert result["json_schema_valid"] is True
    assert result["reported_transaction_rows"] == 1
    assert result["blockers"] == ["original_filing_authenticity_unverified",
                                  "historical_completeness_unverified",
                                  "first_seen_at_unavailable"]
    assert result["ingestion_ready"] is False
    assert result["approved_for_trading"] is False


def test_member_count_one_is_not_trade_count_two():
    payload = body(trade(), trade(filingId="independent-second-trade"))
    r = assess_body(payload, now=NOW)
    assert r["meta_count_member_records_not_trades"] == 1
    assert r["reported_transaction_rows"] == 2
    assert r["json_schema_valid"] is True


def test_stale_cache_and_missing_source_links_reported_independently():
    payload = body(trade(sourceLink=None), fetched="2026-11-01T10:00:00Z")
    result = assess_body(payload, now=NOW)
    assert result["missing_https_filing_links"] == 1
    assert "stale_provider_cache_over_6h" in result["blockers"]
    assert "missing_https_filing_links" in result["blockers"]
    assert "strict_tracefour_contract_failed" in result["blockers"]
    assert result["quality_status"] == "blocked"


def test_future_provider_cache_rejected():
    r = assess_body(body(fetched="2026-11-02T11:00:00Z"), now=NOW)
    assert "future_provider_cache_timestamp" in r["blockers"]


def test_missing_cache_timezone_rejected():
    r = assess_body(body(fetched="2026-11-02T10:00:00"), now=NOW)
    assert "missing_or_naive_cache_timestamp" in r["blockers"]


def test_wrong_identity_rejected():
    b = body()
    b["data"]["member"]["slug"] = "different-member"
    r = assess_body(b, now=NOW)
    assert "wrong_or_missing_member" in r["blockers"]


def test_untrusted_link_host_is_warning_blocker_even_if_https():
    r = assess_body(body(trade(sourceLink="https://example.org/fake.pdf")), now=NOW)
    assert r["untrusted_link_hosts"] == 1
    assert "untrusted_filing_link_hosts" in r["blockers"]


def test_500_record_boundary_blocked():
    r = assess_body(body(*[trade(filingId=f"row-{x}") for x in range(500)]), now=NOW)
    assert "missing_empty_or_endpoint_capped_trades" in r["blockers"]
    assert "strict_tracefour_contract_failed" in r["blockers"]


def test_saved_snapshot_never_claims_live_connection(tmp_path):
    r = diagnose_saved_json(save(tmp_path, body()), now=NOW)
    assert r["source_file_sha256"]
    assert r["live_response_verified"] is False
    assert r["outbound_http_requests"] == 0
    assert r["transport_status"] == "not_tested_by_saved_file"
    assert r["http_status"] is None
    assert r["supabase_writes"] == 0


def test_saved_json_rejects_symlink(tmp_path):
    real = save(tmp_path, body())
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(DiagnosticError, match="regular local"):
        diagnose_saved_json(link)


def test_saved_invalid_json_fails_closed(tmp_path):
    path = tmp_path / "html.json"
    path.write_text("<html>404</html>")
    with pytest.raises(DiagnosticError, match="not UTF-8 JSON"):
        diagnose_saved_json(path)


def test_saved_max_size_rejected(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b"x" * (MAX_JSON_BYTES + 1))
    with pytest.raises(DiagnosticError, match="size limit"):
        diagnose_saved_json(path)


def test_probe_single_get_no_redirect_no_retries_and_closes():
    response = Response(body())
    session = Session(response)
    result = probe_once(session, now=NOW)
    assert result["http_status"] == 200
    assert result["live_response_verified"] is True
    assert result["quality_status"] == "structurally_valid_source_unverified"
    assert result["approved_for_ingestion"] is False
    assert result["outbound_http_requests_attempted"] == 1
    assert response.closed
    assert session.calls == [(TRACEFOUR_URL, {"timeout": REQUEST_TIMEOUT,
                                                "allow_redirects": False,
                                                "stream": True,
                                                "headers": {"Accept": "application/json"}})]


def test_probe_connection_failure_never_invents_http_status():
    session = Session(ConnectionError("Name or service not known"))
    result = probe_once(session, now=NOW)
    assert len(session.calls) == 1
    assert result["http_status"] is None
    assert result["transport_status"] == "failed_before_http_response"
    assert result["live_response_verified"] is False
    assert result["ingestion_ready"] is False


def test_probe_redirect_not_followed_even_when_json_body_available():
    response = Response(body(), status=302)
    r = probe_once(Session(response), now=NOW)
    assert r["transport_status"] == "redirect_blocked"
    assert r["http_status"] == 302
    assert r["live_response_verified"] is False
    assert response.closed


def test_probe_http_503_not_accepted():
    r = probe_once(Session(Response(body(), status=503)), now=NOW)
    assert r["http_status"] == 503
    assert r["transport_status"] == "http_error"
    assert r["live_response_verified"] is False


def test_probe_rejects_html_content_type():
    r = probe_once(Session(Response(body(), headers={"Content-Type": "text/html"})), now=NOW)
    assert r["transport_status"] == "unsupported_content_type"


def test_probe_rejects_json_parse_failure():
    r = probe_once(Session(Response(body(), content=b"not-json")), now=NOW)
    assert r["transport_status"] == "invalid_body"
    assert r["live_response_verified"] is False


def test_probe_rejects_body_over_safe_bound():
    response = Response(body(), chunks=[b"x" * (MAX_JSON_BYTES + 1)])
    r = probe_once(Session(response), now=NOW)
    assert r["transport_status"] == "invalid_body"
    assert response.closed


def test_probe_connection_success_with_missing_links_is_blocked():
    r = probe_once(Session(Response(body(trade(sourceLink=None)))), now=NOW)
    assert r["http_status"] == 200
    assert r["live_response_verified"] is True
    assert r["ingestion_ready"] is False
    assert "missing_https_filing_links" in r["blockers"]


def test_cli_never_exposes_live_get_flags_and_never_fetches(tmp_path, monkeypatch, capsys):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("No network")))
    good = save(tmp_path, body())
    assert main([str(good)]) == 0
    captured = json.loads(capsys.readouterr().out)
    assert captured["transport_status"] == "not_tested_by_saved_file"
    with pytest.raises(SystemExit) as e:
        main(["--allow-network-fetch", str(good)])
    assert e.value.code == 2


def test_actual_uploaded_file_regression_optional():
    file_path = os.getenv("E121_SNAPSHOT_PATH")
    if not file_path:
        pytest.skip("User's saved snapshot intentionally excluded from GitHub CI")
    r = diagnose_saved_json(file_path, now=NOW)
    assert r["source_file_sha256"] == "4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3"
    assert r["reported_transaction_rows"] == 147
    assert r["missing_https_filing_links"] == 21
    assert "stale_provider_cache_over_6h" in r["blockers"]
    assert r["cache_fetched_at_utc"] == "2026-11-02T02:53:54.620000+00:00"
    assert r["outbound_http_requests"] == 0
