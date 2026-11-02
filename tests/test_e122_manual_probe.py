"""E1.22 explicit manual GET opt-in boundaries; ALL tests run offline."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import socket

import pytest

from congress_manual_probe import main, make_no_retry_session, run_manual_probe, ProbeConfigurationError
from congress_connection_diagnostic import TRACEFOUR_URL

NOW = datetime(2026, 11, 2, 10, 6, 40, tzinfo=timezone.utc)


def envelope(**update):
    trade = {
        "memberSlug": "nancy-pelosi", "memberName": "Nancy Pelosi", "filingId": "test-1",
        "sourceLink": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000000.pdf",
        "ticker": "NVDA", "transactionDate": "2026-05-01",
        "disclosureDate": "2026-06-01", "type": "Purchase", "assetType": "Stock",
    }
    trade.update(update)
    return {"data": {"member": {"slug": "nancy-pelosi"}, "trades": [trade]},
            "meta": {"fetchedAt": "2026-11-02T10:00:00Z", "source": "sqlite", "count": 1}}


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    def __init__(self, payload):
        self.payload = payload
        self.closed = False
    def iter_content(self, **_):
        yield json.dumps(self.payload).encode("utf-8")
    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.closed = False
    def get(self, url, **kw):
        self.calls.append((url, kw))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response
    def close(self):
        self.closed = True


def test_no_session_construction_without_two_flags(capsys):
    invoked = []
    def disallowed():
        invoked.append(True)
        raise AssertionError("Session must not be constructed")
    with pytest.raises(SystemExit) as exc:
        main(["--one-get"], session_factory=disallowed, now=NOW)
    assert exc.value.code == 2
    assert invoked == []
    assert "requires --i-approve-one-get" in capsys.readouterr().err


def test_flags_are_mutually_exclusive_before_session_creation(tmp_path):
    source = tmp_path / "saved.json"
    source.write_text(json.dumps(envelope()))
    with pytest.raises(SystemExit) as exc:
        main(["--offline-file", str(source), "--i-approve-one-get"],
             session_factory=lambda: (_ for _ in ()).throw(AssertionError("Unsafe")), now=NOW)
    assert exc.value.code == 2


def test_no_mode_refuses_to_start(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "required" in capsys.readouterr().err


def test_run_manual_probe_explicit_boolean_required():
    factory = lambda: (_ for _ in ()).throw(AssertionError("No session allowed"))
    for approval in (False, None, 1, "true"):
        with pytest.raises(ProbeConfigurationError, match="explicit one-GET"):
            run_manual_probe(approved=approval, session_factory=factory, now=NOW)


def test_offline_file_no_requests(tmp_path, capsys):
    file_path = tmp_path / "source.json"
    file_path.write_text(json.dumps(envelope()))
    result = main(["--offline-file", str(file_path)],
                  session_factory=lambda: (_ for _ in ()).throw(AssertionError("No sessions")), now=NOW)
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["invocation_mode"] == "offline_saved_file_only"
    assert report["outbound_http_requests"] == 0
    assert report["http_status"] is None
    assert report["ingestion_ready"] is False


def test_one_get_mocked_once_and_reported(capsys):
    response = FakeResponse(envelope())
    session = FakeSession(response)
    factory_calls = []
    def factory():
        factory_calls.append(1)
        return session
    rc = main(["--one-get", "--i-approve-one-get"], session_factory=factory, now=NOW)
    assert rc == 0
    assert factory_calls == [1]
    assert len(session.calls) == 1
    assert session.calls[0][0] == TRACEFOUR_URL
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][1]["stream"] is True
    assert session.closed and response.closed
    report = json.loads(capsys.readouterr().out)
    assert report["http_status"] == 200
    assert report["live_response_verified"] is True  # simulated response ONLY
    assert report["approved_for_ingestion"] is False
    assert report["approved_for_trading"] is False
    assert report["scheduled_sync_enabled"] is False


def test_mock_connection_failure_is_exit_three(capsys):
    session = FakeSession(ConnectionError("DNS unavailable"))
    rc = main(["--one-get", "--i-approve-one-get"], session_factory=lambda: session, now=NOW)
    assert rc == 3
    assert session.closed
    report = json.loads(capsys.readouterr().out)
    assert report["http_status"] is None
    assert report["transport_status"] == "failed_before_http_response"


def test_mock_http200_with_missing_filing_link_is_exit_two(capsys):
    response = FakeResponse(envelope(sourceLink=None))
    rc = main(["--one-get", "--i-approve-one-get"],
              session_factory=lambda: FakeSession(response), now=NOW)
    assert rc == 2
    report = json.loads(capsys.readouterr().out)
    assert report["http_status"] == 200
    assert "missing_https_filing_links" in report["blockers"]
    assert report["ingestion_ready"] is False


def test_actual_session_has_zero_http_retries():
    s = make_no_retry_session()
    try:
        retries = s.get_adapter("https://tracefour.com/").max_retries
        assert retries.total == 0
        assert retries.connect == 0
        assert retries.read == 0
        assert retries.status == 0
        assert retries.redirect == 0
    finally:
        s.close()


def test_local_source_request_never_auto_network(monkeypatch, tmp_path):
    def blocked(*a, **k):
        raise AssertionError("Network attempted")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    source = tmp_path / "example.json"
    source.write_text(json.dumps(envelope()))
    assert main(["--offline-file", str(source)], now=NOW) == 0


def test_cli_does_not_offer_unsafe_switches(capsys):
    for flag in ("--approve-database-write", "--schedule", "--submit-order", "--url"):
        with pytest.raises(SystemExit) as exc:
            main([flag])
        assert exc.value.code == 2
    capsys.readouterr()


@pytest.mark.parametrize("url", [
    "https://[invalid-port/ptr.pdf",
    "https://disclosures-clerk.house.gov:bad/public_disc/ptr-pdfs/2026/20000000.pdf",
    "https://disclosures-clerk.house.gov@attacker.test/public_disc/ptr-pdfs/2026/20000000.pdf",
    "https://disclosures-clerk.house.gov:8443/public_disc/ptr-pdfs/2026/20000000.pdf",
    "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000000.pdf?download=1",
    "https://disclosures-clerk.house.gov/other/file.pdf",
])
def test_untrusted_malformed_filing_link_never_crashes(url):
    from congress_connection_diagnostic import assess_body
    report = assess_body(envelope(sourceLink=url), now=NOW)
    assert report["quality_status"] == "blocked"
    assert report["untrusted_link_hosts"] == 1
    assert "untrusted_filing_link_hosts" in report["blockers"]
    assert report["ingestion_ready"] is False
