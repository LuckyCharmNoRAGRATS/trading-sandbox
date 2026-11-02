"""E1.23: offline, quarantine-aware Tracefour congressional research pipeline.

Only consumes an already saved JSON file. NEVER makes HTTP requests, reads
secrets, connects to Supabase, creates orders, or schedules jobs. The permitted
outputs are local research and human-review files, not an ingestion feed.

Reuses the E1.17 remediation, E1.18 reconciliation, E1.19 zero-decision
adjudication, E1.20 PDF-request planner and E1.21 source diagnostics.
The E1.22 manual network probe is deliberately NOT imported or called.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from congress_connection_diagnostic import diagnose_saved_json, DiagnosticError
from congress_remediation import (
    remediate_snapshot, write_research_outputs, RemediationError,
)

VERSION = "E1.23.0"
MAX_RESEARCH_SOURCE_BYTES = 2_000_000
MAX_FUTURE_SKEW = timedelta(minutes=5)


class ResearchPipelineError(ValueError):
    """Invalid source, unsafe release claim, or incomplete review pipeline."""


def _utc(at: datetime | None) -> datetime:
    timestamp = at if at is not None else datetime.now(timezone.utc)
    if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
        raise ResearchPipelineError("Assessment time must be timezone-aware")
    return timestamp.astimezone(timezone.utc)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(item: Any) -> bytes:
    return json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _assert_read_only(rows: list[dict], *, snapshot_sha: str, title: str) -> None:
    if not isinstance(rows, list):
        raise ResearchPipelineError(f"{title}: invalid record collection")
    for row in rows:
        if not isinstance(row, dict):
            raise ResearchPipelineError(f"{title}: record not an object")
        if row.get("eligible_for_ingestion") is not False or row.get("eligible_for_execution") is not False:
            raise ResearchPipelineError(f"{title}: record claims ingestion or execution authority")
        if row.get("first_seen_at") is not None or row.get("official_filing_verified") is not False:
            raise ResearchPipelineError(f"{title}: unproved observation or filing verification")
        if row.get("snapshot_sha256", row.get("source_snapshot_sha256")) != snapshot_sha:
            raise ResearchPipelineError(f"{title}: snapshot lineage changed")
        raw = row.get("raw_record")
        if not isinstance(raw, dict) or row.get("raw_record_sha256") != _digest(_canonical(raw)):
            raise ResearchPipelineError(f"{title}: source row hash mismatch")


def _check_snapshot(snapshot: Path, at: datetime) -> tuple[dict, dict]:
    # Reject URLs and symlinks, and cap source bytes before parsing anything.
    if (not snapshot.is_file() or snapshot.is_symlink()
            or not 0 < snapshot.stat().st_size <= MAX_RESEARCH_SOURCE_BYTES):
        raise ResearchPipelineError("Expected a bounded local JSON snapshot, not URL or symlink")
    try:
        diagnostic = diagnose_saved_json(snapshot, now=at)
    except (DiagnosticError, OSError) as exc:
        raise ResearchPipelineError("Offline snapshot diagnostic refused input") from exc
    raw = snapshot.read_bytes()
    if _digest(raw) != diagnostic["source_file_sha256"]:
        raise ResearchPipelineError("Snapshot changed during diagnostic")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ResearchPipelineError("Snapshot is not UTF-8 JSON") from exc
    meta = body.get("meta", {}) if isinstance(body, dict) else {}
    if (not isinstance(meta, dict) or isinstance(meta.get("count"), bool)
            or meta.get("count") != 1 or not isinstance(meta.get("source"), str)
            or not meta.get("source", "").strip()):
        raise ResearchPipelineError("Member count or provider provenance metadata invalid")
    if diagnostic["member_slug"] != "nancy-pelosi":
        raise ResearchPipelineError("Unexpected member; all rows blocked")
    count = diagnostic.get("reported_transaction_rows")
    if not isinstance(count, int) or not (1 <= count < 500) or diagnostic["malformed_row_objects"]:
        raise ResearchPipelineError("Missing/malformed trades or endpoint completeness limit reached")
    if diagnostic.get("cache_fetched_at_utc") is None:
        raise ResearchPipelineError("Missing timezone-aware provider cache timestamp")
    cache = datetime.fromisoformat(diagnostic["cache_fetched_at_utc"])
    if cache - at > MAX_FUTURE_SKEW:
        raise ResearchPipelineError("Source cache is future-dated beyond clock tolerance")
    # No age cutoff for *offline historical research*. An old provider snapshot
    # is never marked fresh or authorized for ingestion.
    return diagnostic, body


def inspect_snapshot(snapshot_path: str | Path, *, assessed_at: datetime | None = None) -> dict:
    """Pure read of saved JSON; no local output or API invocation."""
    at = _utc(assessed_at)
    path = Path(snapshot_path)
    diagnostics, _ = _check_snapshot(path, at)
    partition = remediate_snapshot(path, assessed_at=at)
    summary = partition["summary"]
    if diagnostics["source_file_sha256"] != summary["source_snapshot_sha256"]:
        raise ResearchPipelineError("Source fingerprint mismatch across E1.17/E1.21")
    clean, quarantine = partition["clean"], partition["quarantine"]
    _assert_read_only(clean + quarantine, snapshot_sha=summary["source_snapshot_sha256"], title="E1.17")
    if len(clean) + len(quarantine) != summary["total_rows"]:
        raise ResearchPipelineError("Record loss at E1.17 boundary")
    if set(row["row_index"] for row in clean + quarantine) != set(range(1, summary["total_rows"] + 1)):
        raise ResearchPipelineError("Discontinuous or duplicated original row indices")
    if len(clean) + len(quarantine) != diagnostics["reported_transaction_rows"]:
        raise ResearchPipelineError("E1.17/E1.21 source count mismatch")
    missing = sum("missing_or_untrusted_official_filing_url" in row["reason_codes"] for row in quarantine)
    if missing < diagnostics["missing_https_filing_links"]:
        raise ResearchPipelineError("Unsafe missing-filing-link promotion")
    if any(row["reason_codes"] or row["quality_status"] != "structurally_clean_source_unverified" for row in clean):
        raise ResearchPipelineError("Clean partition contains unreviewed failures")
    if any(not row["reason_codes"] or row["quality_status"] != "quarantined_review_required" for row in quarantine):
        raise ResearchPipelineError("Quarantine partition missing review reasons")
    normalized_source_ids = [row["research_event_id"] for row in clean if row["research_event_id"]]
    if len(normalized_source_ids) != len(set(normalized_source_ids)):
        raise ResearchPipelineError("Conflicting candidate source identifiers")
    return {"diagnostic": diagnostics, "remediation": partition, "assessment_utc": at.isoformat()}


def build_research_package(snapshot_path: str | Path, output_dir: str | Path, *,
                           assessed_at: datetime | None = None) -> dict:
    """Build E1.17→E1.20 research outputs; never releases data for ingestion.

    Writes into a sibling staging directory. The named output path is created
    only after ALL validations and file generation complete. Existing output
    paths, symlink output folders and symlink sources are refused.
    """
    at = _utc(assessed_at)
    source = Path(snapshot_path)
    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise ResearchPipelineError("Output already exists; refusing overwrite")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ResearchPipelineError("Output parent must be an existing real directory")
    checked = inspect_snapshot(source, assessed_at=at)
    diag = checked["diagnostic"]
    remediation = checked["remediation"]
    snapshot_sha = remediation["summary"]["source_snapshot_sha256"]
    # Lazily import only offline modules: no HTTP clients or broker/database code.
    from congress_reconciliation import reconcile_e117, write_outputs as write_e118
    from congress_adjudication import adjudicate, write_outputs as write_e119
    from congress_evidence_intake import build_request_plan, write_request_plan

    stage = Path(tempfile.mkdtemp(prefix=".e123-stage-", dir=output.parent))
    try:
        first = stage / "e117"
        second = stage / "e118"
        third = stage / "e119"
        fourth = stage / "e120"
        e117_files = write_research_outputs(remediation, first)
        e118 = reconcile_e117(first)
        e118_files = write_e118(e118, second)
        e119 = adjudicate(second, [], second)  # ZERO reviews. Never invent people or PDF evidence.
        e119_files = write_e119(e119, third)
        e120 = build_request_plan(second, pdf_directory=None)  # ZERO external or local PDF lookups.
        e120_files = write_request_plan(e120, fourth)
        # Cross-phase accounting; no unverified row may be silently promoted.
        if (len(e118["research_only"]) != len(remediation["clean"])
                or len(e118["review_queue"]) != len(remediation["quarantine"])
                or len(e118["group_queue"]) != remediation["summary"]["potential_semantic_duplicate_groups"]):
            raise ResearchPipelineError("E1.18 disagrees with E1.17 row counts or duplicate groups")
        if e119["summary"].get("review_events") != 0:
            raise ResearchPipelineError("E1.19 recorded unexpected review evidence")
        if (len(e119["review_queue"]) != len(e118["review_queue"])
                or e119["summary"].get("source_authenticated_pdf_count") != 0
                or any(item.get("review_status") != "awaiting_two_reviewers" for item in e119["review_queue"])):
            raise ResearchPipelineError("E1.19 unexpectedly advanced a pending review")
        if e120["summary"].get("requested_external_urls_accessed") != 0:
            raise ResearchPipelineError("E1.20 reported external source access")
        _assert_read_only(e118["research_only"], snapshot_sha=snapshot_sha, title="E1.18")
        _assert_read_only(e118["review_queue"], snapshot_sha=snapshot_sha, title="E1.18")
        # Source freshness is reporting metadata only. Neither a fresh provider
        # cache nor a 200 response establishes official filing authenticity.
        report = {
            "version": VERSION,
            "status": "NO_GO_READ_ONLY_RESEARCH",
            "source_snapshot_sha256": snapshot_sha,
            "assessment_utc_not_first_seen_at": at.isoformat(),
            "source_transport": "offline_saved_json_not_current_api_connection",
            "user_reported_http200_separate_from_snapshot": True,
            "source_age_seconds": diag.get("cache_age_seconds"),
            "snapshot_quality_status": diag["quality_status"],
            "snapshot_strict_validation_blockers": diag["blockers"],
            "total_source_rows": remediation["summary"]["total_rows"],
            "structurally_clean_source_unverified": len(remediation["clean"]),
            "quarantined_human_review_required": len(remediation["quarantine"]),
            "quarantined_missing_or_untrusted_links": diag["missing_https_filing_links"],
            "ambiguous_actions": remediation["summary"]["quarantine_reason_counts_overlapping"].get("ambiguous_action_other", 0),
            "derivative_stock_label_mismatches": remediation["summary"]["quarantine_reason_counts_overlapping"].get("stock_label_derivative_or_exercise_description", 0),
            "potential_duplicate_groups": len(e118["group_queue"]),
            "human_review_queue_rows": len(e118["review_queue"]),
            "pdf_files_authenticated": 0,
            "reviewer_evidence_events": 0,
            "first_seen_at_established": False,
            "historical_completeness_verified": False,
            "ingestion_approved": False,
            "execution_approved": False,
            "external_http_requests": 0,
            "supabase_writes": 0,
            "broker_orders": 0,
            "scheduled_sync_enabled": False,
            "source_attribution_url": remediation["summary"]["attribution_url"],
            "source_license": remediation["summary"]["license_url"],
            "artifact_hashes_sha256": {},
        }
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                report["artifact_hashes_sha256"][str(file.relative_to(stage))] = _digest(file.read_bytes())
        if not source.is_file() or source.is_symlink() or _digest(source.read_bytes()) != snapshot_sha:
            raise ResearchPipelineError("Source snapshot changed while building review outputs")
        report_file = stage / "e123_manifest.json"
        report_file.write_text(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        # Renaming the completed sibling directory is atomic on one volume.
        # This doesn't convert local file output into ingestion authorization.
        if output.exists() or output.is_symlink():
            raise ResearchPipelineError("Output target appeared during staging; refusing overwrite")
        os.replace(stage, output)
        return {"manifest": report, "output_dir": str(output)}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E1.23 offline Tracefour partition + review pipeline; NO network or trading")
    parser.add_argument("snapshot_json", type=Path, help="already saved JSON path only, NOT a URL")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        written = build_research_package(args.snapshot_json, args.output_dir)
    except (ResearchPipelineError, RemediationError, DiagnosticError, OSError, ValueError) as exc:
        parser.exit(2, f"NO-GO E1.23: {exc}\n")
    print(json.dumps({"status": written["manifest"]["status"],
                      "manifest": str(Path(written["output_dir"]) / "e123_manifest.json"),
                      "clean_unverified": written["manifest"]["structurally_clean_source_unverified"],
                      "quarantined": written["manifest"]["quarantined_human_review_required"],
                      "outbound_http_requests": 0, "supabase_writes": 0, "orders_submitted": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
