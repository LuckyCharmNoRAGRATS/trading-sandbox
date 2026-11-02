"""E1.23 no-network tests for end-to-end contract and fail-closed source checks.

Repository CI additionally exercises real E1.18/E1.19/E1.20 integration when the
modules exist. The local isolated environment uses a stubbed cross-phase test
only when those prior-version modules are not available.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from types import ModuleType

import pytest

from congress_research_pipeline import (
    ResearchPipelineError, build_research_package, inspect_snapshot, main,
)

AT = datetime(2026, 11, 2, 10, 6, 40, tzinfo=timezone.utc)
REAL_SHA = "4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3"


def trade(**kw):
    row = {
        "filingId": "test-1", "memberSlug": "nancy-pelosi", "memberName": "Nancy Pelosi",
        "ticker": "NVDA", "assetType": "Stock", "assetDescription": "Common Stock",
        "type": "Purchase", "owner": "Spouse", "transactionDate": "2026-07-24",
        "disclosureDate": "2026-08-24", "amountLabel": "$1,001 - $15,000",
        "amountMin": 1001, "amountMid": 8001, "amountMax": 15000,
        "ingestedAt": "2026-08-24T16:00:00Z",
        "sourceLink": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf",
    }
    row.update(kw)
    return row


def envelope(*rows, fetched="2026-11-02T10:00:00Z", count=1, source="sqlite"):
    return {
        "data": {"member": {"slug": "nancy-pelosi"}, "trades": list(rows)},
        "meta": {"count": count, "source": source, "fetchedAt": fetched,
                 "attribution": {"page": "https://tracefour.com/trackers/pelosi"},
                 "license": "https://creativecommons.org/licenses/by/4.0/"},
    }


def saved(tmp_path, *rows, **meta):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(envelope(*rows, **meta)), encoding="utf-8")
    return path


def test_missing_original_filing_link_quarantined_without_partial_ingestion(tmp_path):
    source = saved(tmp_path, trade(), trade(filingId="test-2", ticker="MSFT", sourceLink=None))
    r = inspect_snapshot(source, assessed_at=AT)
    assert len(r["remediation"]["clean"]) == 1
    assert len(r["remediation"]["quarantine"]) == 1
    assert "missing_or_untrusted_official_filing_url" in r["remediation"]["quarantine"][0]["reason_codes"]
    assert "strict_tracefour_contract_failed" in r["diagnostic"]["blockers"]
    assert r["diagnostic"]["json_schema_valid"] is False
    assert all(x["eligible_for_ingestion"] is False and x["eligible_for_execution"] is False
               for x in r["remediation"]["clean"] + r["remediation"]["quarantine"])


def test_option_retained_as_option_in_research_only(tmp_path):
    source = saved(tmp_path, trade(assetType="Stock Option", assetDescription="Call Option"))
    r = inspect_snapshot(source, assessed_at=AT)
    row = r["remediation"]["clean"][0]
    assert row["asset_type_reported"] == "Stock Option"
    assert row["owner_type"] == "Spouse"
    assert row["first_seen_at"] is None
    assert row["official_filing_verified"] is False


def test_ambiguous_other_not_reinterpreted_as_sale(tmp_path):
    source = saved(tmp_path, trade(type="Other", filingId="nancy-pelosi|NVDA|2026-07-24|Sale (Full)|Spouse"))
    row = inspect_snapshot(source, assessed_at=AT)["remediation"]["quarantine"][0]
    assert row["action_reported"] == "Other"
    assert "source_id_suggests_sale_but_type_other" in row["reason_codes"]


def test_semantic_duplicate_pair_fully_quarantined(tmp_path):
    path = saved(tmp_path, trade(filingId="a"), trade(filingId="b", disclosureDate="2026-08-25"))
    r = inspect_snapshot(path, assessed_at=AT)
    assert len(r["remediation"]["clean"]) == 0
    assert len(r["remediation"]["quarantine"]) == 2
    assert r["remediation"]["summary"]["potential_semantic_duplicate_groups"] == 1
    assert all("potential_semantic_duplicate" in x["reason_codes"] for x in r["remediation"]["quarantine"])


def test_stale_cached_json_still_research_only_and_not_live_connection(tmp_path):
    path = saved(tmp_path, trade(), fetched="2026-10-20T10:00:00Z")
    r = inspect_snapshot(path, assessed_at=AT)
    assert "stale_provider_cache_over_6h" in r["diagnostic"]["blockers"]
    assert r["diagnostic"]["live_response_verified"] is False
    assert len(r["remediation"]["clean"]) == 1
    assert r["remediation"]["clean"][0]["first_seen_at"] is None


@pytest.mark.parametrize("bad", ["count", "provider", "slug", "fetched", "future", "attribution", "rows"])
def test_malformed_envelopes_refuse_all_outputs(tmp_path, bad):
    data = envelope(trade())
    if bad == "count": data["meta"]["count"] = 2
    if bad == "provider": data["meta"]["source"] = ""
    if bad == "slug": data["data"]["member"]["slug"] = "other-member"
    if bad == "fetched": data["meta"]["fetchedAt"] = "2026-11-02T10:00:00"
    if bad == "future": data["meta"]["fetchedAt"] = "2026-11-02T12:00:00Z"
    if bad == "attribution": data["meta"].pop("attribution")
    if bad == "rows": data["data"]["trades"] = [trade(filingId=str(i)) for i in range(500)]
    src = tmp_path / "bad.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    dest = tmp_path / "output"
    with pytest.raises((ResearchPipelineError, ValueError)):
        build_research_package(src, dest, assessed_at=AT)
    assert not dest.exists()
    assert not list(tmp_path.glob(".e123-stage-*"))


def test_symlink_snapshot_rejected(tmp_path):
    real = saved(tmp_path, trade())
    alias = tmp_path / "snapshot-alias.json"
    alias.symlink_to(real)
    with pytest.raises(ResearchPipelineError, match="local JSON snapshot"):
        inspect_snapshot(alias, assessed_at=AT)


def test_invalid_assessment_clock_rejected(tmp_path):
    p = saved(tmp_path, trade())
    with pytest.raises(ResearchPipelineError, match="timezone-aware"):
        inspect_snapshot(p, assessed_at=datetime(2026, 11, 2))


def test_real_uploaded_snapshot_read_only_regression_optional():
    raw_path = os.getenv("E123_SNAPSHOT_PATH")
    if not raw_path:
        pytest.skip("Private Tracefour snapshot is never committed to the repository")
    path = Path(raw_path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == REAL_SHA
    out = inspect_snapshot(path, assessed_at=AT)
    summary = out["remediation"]["summary"]
    assert summary["total_rows"] == 147
    assert len(out["remediation"]["clean"]) == 75
    assert len(out["remediation"]["quarantine"]) == 72
    assert summary["potential_semantic_duplicate_groups"] == 12
    assert out["diagnostic"]["missing_https_filing_links"] == 21
    reasons = summary["quarantine_reason_counts_overlapping"]
    assert reasons["ambiguous_action_other"] == 34
    assert reasons["stock_label_derivative_or_exercise_description"] == 10
    assert out["diagnostic"]["outbound_http_requests"] == 0
    assert all(x["eligible_for_ingestion"] is False for x in out["remediation"]["clean"])


def _stub_downstream(monkeypatch, *, fail_phase=None, promote=False):
    """Stub downstream IO only for isolated local test; repository CI tests real modules."""
    from congress_remediation import _sha_obj
    reconciler = ModuleType("congress_reconciliation")
    adjudicator = ModuleType("congress_adjudication")
    intake = ModuleType("congress_evidence_intake")
    def reconcile_e117(root):
        if fail_phase == "reconcile": raise ValueError("deliberate E1.18 failure")
        clean = [json.loads(line) for line in (root / "pelosi_e117_clean_research.jsonl").read_text().splitlines()]
        quarantine = [json.loads(line) for line in (root / "pelosi_e117_quarantined_review.jsonl").read_text().splitlines()]
        groups = sorted({x["semantic_group_id"] for x in quarantine if x["semantic_group_id"]})
        return {"research_only": clean, "review_queue": quarantine, "group_queue": [{"group_id": x} for x in groups]}
    def write_fake(result, folder):
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / "fake_manifest.json"
        p.write_text(json.dumps({"records": len(result.get("review_queue", []))}, sort_keys=True))
        return {"manifest": {"stub": True}, "paths": {"manifest": p}}
    def adjudicate(root, decisions, pdf_dir):
        assert decisions == []
        if fail_phase == "adjudicate": raise ValueError("deliberate E1.19 failure")
        src = reconcile_e117(root.parent / "e117")
        statuses = [{"review_status": "awaiting_two_reviewers"} for _ in src["review_queue"]]
        return {"summary": {"review_events": 0, "source_authenticated_pdf_count": 0}, "review_queue": statuses, "ledger": []}
    def build_request_plan(root, *, pdf_directory):
        assert pdf_directory is None
        if fail_phase == "intake": raise ValueError("deliberate E1.20 failure")
        return {"summary": {"requested_external_urls_accessed": 0}, "document_requests": []}
    reconciler.reconcile_e117 = reconcile_e117
    reconciler.write_outputs = write_fake
    adjudicator.adjudicate = adjudicate
    adjudicator.write_outputs = write_fake
    intake.build_request_plan = build_request_plan
    intake.write_request_plan = write_fake
    monkeypatch.setitem(sys.modules, "congress_reconciliation", reconciler)
    monkeypatch.setitem(sys.modules, "congress_adjudication", adjudicator)
    monkeypatch.setitem(sys.modules, "congress_evidence_intake", intake)


def test_local_staged_pipeline_is_deterministic_and_read_only(tmp_path, monkeypatch):
    _stub_downstream(monkeypatch)
    path = saved(tmp_path, trade(), trade(filingId="second", ticker="MSFT", sourceLink=None))
    a = build_research_package(path, tmp_path / "a", assessed_at=AT)
    b = build_research_package(path, tmp_path / "b", assessed_at=AT)
    assert a["manifest"] == b["manifest"]
    assert a["manifest"]["total_source_rows"] == 2
    assert a["manifest"]["structurally_clean_source_unverified"] == 1
    assert a["manifest"]["quarantined_human_review_required"] == 1
    assert a["manifest"]["ingestion_approved"] is False
    assert a["manifest"]["execution_approved"] is False
    assert a["manifest"]["external_http_requests"] == 0
    assert a["manifest"]["pdf_files_authenticated"] == 0
    assert len(a["manifest"]["artifact_hashes_sha256"]) >= 4
    for rel, sha in a["manifest"]["artifact_hashes_sha256"].items():
        assert hashlib.sha256((tmp_path / "a" / rel).read_bytes()).hexdigest() == sha
    assert not list(tmp_path.glob(".e123-stage-*"))
    with pytest.raises(ResearchPipelineError, match="already exists"):
        build_research_package(path, tmp_path / "a", assessed_at=AT)


@pytest.mark.parametrize("failed_phase", ["reconcile", "adjudicate", "intake"])
def test_cross_phase_failure_never_publishes_partial_directory(tmp_path, monkeypatch, failed_phase):
    _stub_downstream(monkeypatch, fail_phase=failed_phase)
    src = saved(tmp_path, trade())
    output = tmp_path / "not_published"
    with pytest.raises(ValueError, match="deliberate"):
        build_research_package(src, output, assessed_at=AT)
    assert not output.exists()
    assert not list(tmp_path.glob(".e123-stage-*"))


def test_client_entrypoint_rejects_urls_or_unapproved_network_flags(tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["https://tracefour.com/v1/congress/nancy-pelosi", "--output-dir", str(tmp_path / "out")])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["--allow-network-fetch", "--output-dir", str(tmp_path / "out")])
    assert e.value.code == 2


def test_external_network_is_blocked(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs): raise AssertionError("Network not approved")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    r = inspect_snapshot(saved(tmp_path, trade()), assessed_at=AT)
    assert r["diagnostic"]["outbound_http_requests"] == 0


def test_real_modules_integration_optional(tmp_path):
    import importlib.util
    if any(importlib.util.find_spec(name) is None for name in (
        "congress_reconciliation", "congress_adjudication", "congress_evidence_intake"
    )):
        pytest.skip("Additional E1.18–E1.20 modules not mounted in isolated local test workspace")
    path = saved(tmp_path, trade(), trade(filingId="second", ticker="MSFT", sourceLink=None))
    result = build_research_package(path, tmp_path / "actual", assessed_at=AT)
    assert result["manifest"]["quarantined_human_review_required"] == 1
    assert result["manifest"]["human_review_queue_rows"] == 1
    assert (tmp_path / "actual/e118/pelosi_e118_human_review_queue.jsonl").is_file()
    assert (tmp_path / "actual/e119/pelosi_e119_audit_ledger.jsonl").read_bytes() == b""
    assert (tmp_path / "actual/e120/pelosi_e120_house_pdf_request_manifest.csv").is_file()
