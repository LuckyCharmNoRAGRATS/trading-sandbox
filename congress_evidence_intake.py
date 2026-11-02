"""E1.20: offline House-filing request inventory and local PDF intake preflight.

Inputs: E1.18 hash-checked review queue; optionally user-supplied local PDFs.
This module performs NO HTTP, broker or database calls. It never authenticates
House origin, automatically resolves any E1.18 task, or creates trade signals.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from pypdf import PdfReader
from congress_adjudication import AdjudicationError, canonical, digest, load_e118

VERSION = "E1.20.0"
OFFICIAL_HOST = "disclosures-clerk.house.gov"
OFFICIAL_PATH = re.compile(r"/public_disc/ptr-pdfs/(20\d{2})/(\d{5,12})\.pdf\Z")
MAX_PDF_BYTES = 20_000_000
MAX_PAGES = 500
MAX_CSV_ROWS = 499

REQUEST_COLUMNS = (
    "request_id", "review_priority", "source_url_unverified", "expected_local_filename",
    "source_document_year", "source_document_id", "linked_task_count", "linked_row_indices",
    "linked_task_ids", "semantic_group_ids", "action_ambiguities", "derivative_ambiguities",
    "local_pdf_status", "local_pdf_sha256", "local_pdf_page_count", "pdf_origin_authenticated",
    "official_filing_verified", "eligible_for_ingestion", "eligible_for_execution",
)
MANUAL_COLUMNS = (
    "task_id", "row_index", "review_priority", "ticker", "transaction_date", "filing_date",
    "reason_codes", "suggested_evidence_url_unverified", "required_next_action",
    "official_filing_url", "verified_link", "pdf_origin_authenticated", "eligible_for_ingestion",
    "eligible_for_execution",
)
GROUP_COLUMNS = (
    "semantic_group_id", "row_indices", "member_count", "linked_source_url_count",
    "linked_source_urls_unverified", "missing_link_row_indices", "all_linked_documents_observed_locally",
    "resolved", "review_instruction",
)


class IntakeError(ValueError):
    """Invalid source, suspicious local file or unverifiable intake state."""


def _csv_safe(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, dict)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    s = str(value)
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) else s


def _official_link_parts(value: Any) -> tuple[str, str, str] | None:
    """Only produce *filename suggestions* from exact apparent House PDF URLs.

    Do NOT open links or mark provider-submitted URLs authenticated.
    """
    if not isinstance(value, str) or len(value) > 400:
        return None
    try:
        p = urlsplit(value)
    except ValueError:
        return None
    if p.scheme != "https" or p.netloc != OFFICIAL_HOST or p.query or p.fragment or p.username or p.password:
        return None
    m = OFFICIAL_PATH.fullmatch(p.path)
    if m is None:
        return None
    return m.group(1), m.group(2), f"{m.group(1)}_{m.group(2)}.pdf"


def _read_local_pdf(local_dir: Path, name: str) -> dict:
    """Read one permitted expected filename; check syntax only, never origin."""
    if not re.fullmatch(r"20\d{2}_\d{5,12}\.pdf", name):
        raise IntakeError("Untrusted local PDF basename")
    path = local_dir / name
    if path.is_symlink() or not path.is_file():
        return {"status": "rejected_not_regular_file_or_symlink", "sha256": None, "page_count": None}
    try:
        size = path.stat().st_size
        if not (0 < size <= MAX_PDF_BYTES):
            raise IntakeError("Local PDF size outside limit")
        raw = path.read_bytes()
        if len(raw) != size or not raw.startswith(b"%PDF-"):
            raise IntakeError("File is not a PDF or changed during read")
        # No OCR or interpretation. Merely ensure parseable pages are present.
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise IntakeError("Encrypted local PDF is unsupported")
        count = len(reader.pages)
        if not (1 <= count <= MAX_PAGES):
            raise IntakeError("Invalid page count")
        return {"status": "local_pdf_parseable_origin_unverified",
                "sha256": digest(raw), "page_count": count}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"status": "rejected_local_pdf_validation", "sha256": None,
                "page_count": None, "error": str(exc)[:160]}
    except Exception:
        # pypdf parser may raise many specialised parsing exceptions.
        return {"status": "rejected_local_pdf_validation", "sha256": None,
                "page_count": None, "error": "PDF parser rejected supplied bytes"}


def build_request_plan(e118_directory: str | Path, *, pdf_directory: str | Path | None = None) -> dict:
    """Deterministic plan over all quarantined E1.18 tasks, with no URL access."""
    base = Path(e118_directory)
    source_manifest, queue = load_e118(base)
    if not 0 < len(queue) <= MAX_CSV_ROWS:
        raise IntakeError("No eligible E1.18 review queue")
    source_manifest_hash = digest((base / "pelosi_e118_manifest.json").read_bytes())
    linked = defaultdict(list)
    missing = []
    task_ids_seen = set()
    all_group_members = defaultdict(list)
    for row in queue:
        task_id = row["task_id"]
        if task_id in task_ids_seen:
            raise IntakeError("Duplicate task ID")
        task_ids_seen.add(task_id)
        group = row.get("semantic_group_id")
        if group:
            if not isinstance(group, str) or not group.strip():
                raise IntakeError("Malformed semantic group")
            all_group_members[group].append(row)
        source_url = row.get("official_filing_url")
        parts = _official_link_parts(source_url)
        if parts:
            linked[(parts[0], parts[1], source_url)].append(row)
        else:
            # A suggested URL from a related row remains an unverified hint;
            # NEVER inherit it as the missing row's official provenance.
            missing.append({
                "task_id": task_id, "row_index": row["row_index"],
                "review_priority": row.get("review_priority", "P3"),
                "ticker": row.get("ticker"), "transaction_date": row.get("transaction_date"),
                "filing_date": row.get("filing_date"),
                "reason_codes": row.get("reason_codes", []),
                "suggested_evidence_url_unverified": row.get("suggested_evidence_url_unverified", []),
                "required_next_action": "independently_locate_house_filing_then_match_transaction_line",
                "official_filing_url": None,
                "verified_link": False,
                "pdf_origin_authenticated": False,
                "eligible_for_ingestion": False, "eligible_for_execution": False,
            })
    folder = Path(pdf_directory) if pdf_directory is not None else None
    if folder is not None and (not folder.is_dir() or folder.is_symlink()):
        raise IntakeError("Supplied local PDF folder is absent or symlinked")
    requests = []
    for (year, number, url), tasks in linked.items():
        # The basename is based on the *source-supplied* URL, not independently verified.
        filename = f"{year}_{number}.pdf"
        observed = (_read_local_pdf(folder, filename) if folder is not None and (folder / filename).exists()
                    else {"status": "awaiting_user_supplied_pdf", "sha256": None, "page_count": None})
        reasons = Counter(x for task in tasks for x in task.get("reason_codes", []))
        row_indices = sorted(x["row_index"] for x in tasks)
        requests.append({
            "request_id": "e120-" + digest(canonical({"snapshot": source_manifest["source_sha256"], "url": url}))[:20],
            "review_priority": "P1" if any(x.get("review_priority") == "P1" for x in tasks) else "P2",
            "source_url_unverified": url,
            "expected_local_filename": filename,
            "source_document_year": year, "source_document_id": number,
            "linked_task_count": len(tasks), "linked_row_indices": row_indices,
            "linked_task_ids": sorted(x["task_id"] for x in tasks),
            "semantic_group_ids": sorted({x["semantic_group_id"] for x in tasks if x.get("semantic_group_id")}),
            "action_ambiguities": reasons["ambiguous_action_other"],
            "derivative_ambiguities": reasons["stock_label_derivative_or_exercise_description"],
            "local_pdf_status": observed["status"],
            "local_pdf_sha256": observed["sha256"], "local_pdf_page_count": observed["page_count"],
            "pdf_origin_authenticated": False, "official_filing_verified": False,
            "eligible_for_ingestion": False, "eligible_for_execution": False,
        })
    requests.sort(key=lambda x: (x["review_priority"] != "P1", -x["linked_task_count"], x["source_url_unverified"]))
    missing.sort(key=lambda x: (x["review_priority"] != "P1", x["row_index"]))
    by_url = {x["source_url_unverified"]: x for x in requests}
    groups = []
    for gid, rows in sorted(all_group_members.items()):
        if len(rows) < 2 or any("potential_semantic_duplicate" not in x.get("reason_codes", []) for x in rows):
            raise IntakeError("Invalid semantic duplicate-group lineage")
        urls = sorted({x["official_filing_url"] for x in rows if _official_link_parts(x.get("official_filing_url"))})
        absent = sorted(x["row_index"] for x in rows if not _official_link_parts(x.get("official_filing_url")))
        groups.append({
            "semantic_group_id": gid,
            "row_indices": sorted(x["row_index"] for x in rows), "member_count": len(rows),
            "linked_source_url_count": len(urls), "linked_source_urls_unverified": urls,
            "missing_link_row_indices": absent,
            "all_linked_documents_observed_locally": bool(urls) and not absent and all(
                by_url[u]["local_pdf_status"] == "local_pdf_parseable_origin_unverified" for u in urls),
            "resolved": False,
            "review_instruction": "compare_every_group_original_filing_line; do_not_auto_merge",
        })
    if sum(x["linked_task_count"] for x in requests) + len(missing) != len(queue):
        raise IntakeError("Lost or duplicated E1.18 tasks")
    return {"summary": {
        "version": VERSION,
        "source_snapshot_sha256": source_manifest["source_sha256"],
        "e118_manifest_sha256": source_manifest_hash,
        "total_source_rows": source_manifest["source_rows"],
        "quarantined_tasks": len(queue),
        "linked_tasks": sum(x["linked_task_count"] for x in requests),
        "missing_or_untrusted_link_tasks": len(missing),
        "distinct_document_requests": len(requests),
        "potential_duplicate_groups": len(groups),
        "parseable_local_pdfs_origin_unverified": sum(x["local_pdf_status"] == "local_pdf_parseable_origin_unverified" for x in requests),
        "requested_external_urls_accessed": 0,
        "house_pdf_origin_authenticated": 0,
        "official_filing_verified": 0, "reviewer_decisions": 0,
        "source_ambiguities_automatically_resolved": 0,
        "ingestion_approved": False, "trading_approved": False,
        "supabase_writes": 0, "broker_orders": 0,
        "decision": "NO_GO_AWAITING_USER_SUPPLIED_AUTHENTICATED_HOUSE_PDFS",
    }, "document_requests": requests, "missing_provenance_queue": missing, "duplicate_groups": groups}


def _write_csv(filename: Path, rows: list[dict], cols: tuple[str, ...]):
    with filename.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_safe(row.get(k)) for k in cols})


def write_request_plan(result: dict, destination: str | Path) -> dict:
    """Create local, self-checksummed review exports; no network or database IO."""
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {
        "document_requests": folder / "pelosi_e120_house_pdf_request_manifest.csv",
        "manual_provenance": folder / "pelosi_e120_missing_provenance_queue.csv",
        "duplicate_groups": folder / "pelosi_e120_duplicate_group_evidence.csv",
        "manifest": folder / "pelosi_e120_manifest.json",
    }
    _write_csv(paths["document_requests"], result["document_requests"], REQUEST_COLUMNS)
    _write_csv(paths["manual_provenance"], result["missing_provenance_queue"], MANUAL_COLUMNS)
    _write_csv(paths["duplicate_groups"], result["duplicate_groups"], GROUP_COLUMNS)
    file_hashes = {p.name: digest(p.read_bytes()) for k, p in paths.items() if k != "manifest"}
    manifest = {**result["summary"], "local_artifact_sha256": dict(sorted(file_hashes.items()))}
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"paths": paths, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("e118_dir", type=Path, help="hash-verified E1.18 local output directory")
    p.add_argument("--local-pdf-dir", type=Path, help="optional directory of files explicitly provided by user")
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args(argv)
    try:
        report = write_request_plan(build_request_plan(a.e118_dir, pdf_directory=a.local_pdf_dir), a.output_dir)
    except (AdjudicationError, IntakeError, OSError, ValueError) as exc:
        p.exit(2, f"NO-GO E1.20: {exc}\n")
    print(json.dumps({"summary": report["manifest"], "files": {k: str(v) for k, v in report["paths"].items()}},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
