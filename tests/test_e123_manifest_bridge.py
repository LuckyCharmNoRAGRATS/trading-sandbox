"""E1.23 E1.18→E1.19/E1.20 dual-manifest schema compatibility; entirely synthetic."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from congress_adjudication import (
    AdjudicationError, adjudicate, canonical, digest, load_e118,
)
from congress_evidence_intake import build_request_plan


def create_manifest(tmp_path: Path, *, use_current_schema: bool) -> Path:
    folder = tmp_path / ("current" if use_current_schema else "legacy")
    folder.mkdir()
    raw = {"filingId": "synthetic-1", "memberName": "Nancy Pelosi", "ticker": "NVDA",
           "assetDescription": "Common Stock", "transactionDate": "2026-07-24",
           "disclosureDate": "2026-08-24", "type": "Purchase"}
    task = {
        "task_id": "e118-synthetic-1", "row_index": 1,
        "queue_status": "pending_original_filing_evidence",
        "review_priority": "P2", "ticker": "NVDA", "transaction_date": "2026-07-24",
        "filing_date": "2026-08-24", "official_filing_url": None,
        "reason_codes": ["missing_or_untrusted_official_filing_url"],
        "snapshot_sha256": "a" * 64,
        "raw_record": raw, "raw_record_sha256": digest(canonical(raw)),
        "semantic_group_id": None, "source_filing_id": "synthetic-1",
        "eligible_for_ingestion": False, "eligible_for_execution": False,
        "official_filing_verified": False, "first_seen_at": None,
    }
    clean = {"row_index": 2, "snapshot_sha256": "a" * 64,
             "eligible_for_ingestion": False, "eligible_for_execution": False,
             "official_filing_verified": False, "first_seen_at": None}
    files = {
        "pelosi_e118_human_review_queue.jsonl": canonical(task) + b"\n",
        "pelosi_e118_research_only.jsonl": canonical(clean) + b"\n",
        "pelosi_e118_semantic_duplicate_groups.csv": b"group_id,row_indices,number_of_rows\n",
    }
    for name, data in files.items():
        (folder / name).write_bytes(data)
    hashes = {name: digest(data) for name, data in files.items()}
    if use_current_schema:
        manifest = {
            "version": "E1.18.1", "total_source_rows": 2,
            "source_snapshot_sha256": "a" * 64, "clean_research_only_unverified_rows": 1,
            "quarantined_human_review_rows": 1,
            "semantic_duplicate_groups_pending": 0,
            "auto_reclassified_rows": 0, "auto_deduplicated_rows": 0,
            "source_verified_rows": 0, "original_pdf_bytes_reviewed": 0,
            "first_seen_at_estimated": False,
            "ingestion_approved": False, "execution_approved": False,
            "local_output_sha256": hashes,
        }
    else:
        manifest = {"source_rows": 2, "source_sha256": "a" * 64,
                    "clean_unverified": 1, "review_queue": 1,
                    "auto_promotions": 0, "original_pdf_files_reviewed": 0,
                    "approval_for_ingestion": False, "approval_for_trading": False,
                    "output_sha256": hashes}
    (folder / "pelosi_e118_manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return folder


@pytest.mark.parametrize("use_current_schema", [False, True])
def test_both_e118_manifest_versions_load_and_preserve_no_go(tmp_path, use_current_schema):
    root = create_manifest(tmp_path, use_current_schema=use_current_schema)
    manifest, queue = load_e118(root)
    assert len(queue) == 1
    assert manifest["source_rows"] == 2
    assert manifest["source_sha256"] == "a" * 64
    adjudicated = adjudicate(root, [], root)
    assert adjudicated["summary"]["review_events"] == 0
    assert adjudicated["review_queue"][0]["review_status"] == "awaiting_two_reviewers"
    assert adjudicated["review_queue"][0]["eligible_for_execution"] is False
    request = build_request_plan(root, pdf_directory=None)
    assert request["summary"]["quarantined_tasks"] == 1
    assert request["summary"]["missing_or_untrusted_link_tasks"] == 1
    assert request["summary"]["requested_external_urls_accessed"] == 0


@pytest.mark.parametrize("field,value", [
    ("source_verified_rows", 1), ("first_seen_at_estimated", True),
    ("auto_reclassified_rows", 1), ("auto_deduplicated_rows", 2),
    ("original_pdf_bytes_reviewed", 1),
    ("ingestion_approved", True), ("execution_approved", True),
])
def test_current_format_unsafe_claim_refused(tmp_path, field, value):
    root = create_manifest(tmp_path, use_current_schema=True)
    f = root / "pelosi_e118_manifest.json"
    j = json.loads(f.read_text());j[field] = value
    f.write_text(json.dumps(j))
    with pytest.raises(AdjudicationError, match="Unsafe or incomplete"):
        load_e118(root)


def test_conflicting_manifest_alias_is_rejected(tmp_path):
    root = create_manifest(tmp_path, use_current_schema=True)
    path = root / "pelosi_e118_manifest.json"
    obj = json.loads(path.read_text());obj["source_rows"] = 999
    path.write_text(json.dumps(obj))
    with pytest.raises(AdjudicationError, match="Conflicting"):
        load_e118(root)


def test_bad_current_manifest_hash_refused(tmp_path):
    root = create_manifest(tmp_path, use_current_schema=True)
    path = root / "pelosi_e118_manifest.json"
    obj = json.loads(path.read_text());obj["local_output_sha256"]["pelosi_e118_human_review_queue.jsonl"] = "0"*64
    path.write_text(json.dumps(obj))
    with pytest.raises(AdjudicationError, match="SHA-256 mismatch"):
        load_e118(root)
