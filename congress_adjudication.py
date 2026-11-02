"""E1.19: deterministic offline two-reviewer Pelosi disclosure adjudication.

No network, Supabase, broker API, deployment or external PDF fetching.
Reading a supplied PDF and matching an excerpt proves only local byte/text
consistency, not that the PDF is an authentic House filing or that the trade
really took place. Nothing in this module grants ingestion/trading clearance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, date
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from pypdf import PdfReader

VERSION = "E1.19.0"
MANIFEST_FILE = "pelosi_e118_manifest.json"
QUEUE_FILE = "pelosi_e118_human_review_queue.jsonl"
GROUP_FILE = "pelosi_e118_semantic_duplicate_groups.csv"
RESEARCH_FILE = "pelosi_e118_research_only.jsonl"
MAX_FILE_BYTES = 20_000_000
MAX_QUEUE_ROWS = 499
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
REVIEWER_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,63}\Z")
ALLOWED_ACTIONS = {"buy", "sell", "exchange", "other", "unknown"}
ALLOWED_ASSETS = {"stock", "option", "exercise", "other", "unknown"}
ALLOWED_DUPLICATE = {"distinct_transaction", "same_transaction", "amendment", "unknown"}
ROLES = ("primary", "secondary")
REVIEW_COLUMNS = (
    "task_id", "row_index", "role", "reviewer_id", "pdf_filename", "pdf_sha256",
    "page_number", "transaction_excerpt", "action_verdict", "asset_verdict",
    "duplicate_verdict", "reviewed_at_utc", "notes"
)


class AdjudicationError(ValueError):
    """Unverifiable or conflicting evidence must fail closed."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_bytes(path: Path, *, max_size: int = MAX_FILE_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= max_size:
        raise AdjudicationError(f"Missing, symlinked, empty or oversized file: {path.name}")
    return path.read_bytes()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_safe_bytes(path).decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise AdjudicationError(f"Invalid JSON: {path.name}") from exc



def _compatible_e118_manifest(source: Any) -> dict:
    """Adapt E1.18's current schema without altering its SHA-256 evidence.

    E1.18.1 writes newer descriptive keys; E1.19/20 originally read the
    earlier, more compact schema. Normalize *in memory* and reject any
    contradictory alias or new-format authority/verification claim.
    The original manifest bytes remain untouched for ledger anchoring.
    """
    if not isinstance(source, dict):
        raise AdjudicationError("Invalid E1.18 manifest object")
    if "total_source_rows" not in source:
        return source  # Previously published compact E1.18 manifest.
    for gate, required in (
        ("auto_reclassified_rows", 0), ("auto_deduplicated_rows", 0),
        ("source_verified_rows", 0), ("original_pdf_bytes_reviewed", 0),
        ("ingestion_approved", False), ("execution_approved", False),
        ("first_seen_at_estimated", False),
    ):
        if type(source.get(gate)) is not type(required) or source[gate] != required:
            raise AdjudicationError("Unsafe or incomplete E1.18 manifest: " + gate)
    aliases = {
        "source_rows": "total_source_rows",
        "source_sha256": "source_snapshot_sha256",
        "clean_unverified": "clean_research_only_unverified_rows",
        "review_queue": "quarantined_human_review_rows",
        "auto_promotions": "auto_reclassified_rows",
        "original_pdf_files_reviewed": "original_pdf_bytes_reviewed",
        "approval_for_ingestion": "ingestion_approved",
        "approval_for_trading": "execution_approved",
        "output_sha256": "local_output_sha256",
    }
    result = dict(source)
    for old, current in aliases.items():
        if current not in source:
            raise AdjudicationError("Missing current E1.18 manifest field: " + current)
        if old in source and source[old] != source[current]:
            raise AdjudicationError("Conflicting E1.18 manifest field aliases: " + old)
        result[old] = source[current]
    # Distinct duplicate groups must not be silently claimed as resolved.
    if type(source.get("semantic_duplicate_groups_pending")) is not int:
        raise AdjudicationError("Missing semantic duplicate group count")
    return result


def load_e118(directory: str | Path) -> tuple[dict, list[dict]]:
    """Verify E1.18 manifest, queue, all preserved data and frozen statuses."""
    base = Path(directory)
    manifest = _compatible_e118_manifest(_read_json(base / MANIFEST_FILE))
    if (not isinstance(manifest, dict) or type(manifest.get("source_rows")) is not int
            or not 1 <= manifest["source_rows"] < 500):
        raise AdjudicationError("Invalid E1.18 source manifest")
    if manifest.get("approval_for_ingestion") is not False or manifest.get("approval_for_trading") is not False:
        raise AdjudicationError("Unsafe source-manifest approval flags")
    if manifest.get("auto_promotions") != 0 or manifest.get("original_pdf_files_reviewed") != 0:
        raise AdjudicationError("Unexpected E1.18 verification claims")
    checksums = manifest.get("output_sha256")
    if not isinstance(checksums, dict):
        raise AdjudicationError("E1.18 file-hash map missing")
    for filename in (QUEUE_FILE, GROUP_FILE, RESEARCH_FILE):
        expected = checksums.get(filename)
        if not isinstance(expected, str) or not HASH_RE.fullmatch(expected):
            raise AdjudicationError(f"Invalid digest for {filename}")
        if digest(_safe_bytes(base / filename)) != expected:
            raise AdjudicationError(f"E1.18 file SHA-256 mismatch: {filename}")
    try:
        queue = [json.loads(x) for x in (base / QUEUE_FILE).read_text(encoding="utf-8").splitlines() if x.strip()]
        research = [json.loads(x) for x in (base / RESEARCH_FILE).read_text(encoding="utf-8").splitlines() if x.strip()]
    except (ValueError, UnicodeError, OSError) as exc:
        raise AdjudicationError("E1.18 source JSONL malformed") from exc
    if not (len(queue) == manifest.get("review_queue") and len(research) == manifest.get("clean_unverified")
            and len(queue) + len(research) == manifest.get("source_rows")
            and len(queue) <= MAX_QUEUE_ROWS):
        raise AdjudicationError("E1.18 partition counts disagree")
    seen_ids, indexes = set(), set()
    for item in queue:
        if not isinstance(item, dict) or not isinstance(item.get("task_id"), str):
            raise AdjudicationError("Malformed E1.18 review task")
        if item["task_id"] in seen_ids or type(item.get("row_index")) is not int:
            raise AdjudicationError("Duplicate E1.18 task ID or bad row index")
        seen_ids.add(item["task_id"])
        indexes.add(item["row_index"])
        if (item.get("queue_status") != "pending_original_filing_evidence"
                or item.get("eligible_for_ingestion") is not False
                or item.get("eligible_for_execution") is not False
                or item.get("official_filing_verified") is not False
                or item.get("first_seen_at") is not None):
            raise AdjudicationError("Unjustified review task promotion")
        if item.get("snapshot_sha256") != manifest.get("source_sha256"):
            raise AdjudicationError("Task source snapshot hash mismatch")
        raw = item.get("raw_record")
        if (not isinstance(raw, dict) or item.get("raw_record_sha256") != digest(canonical(raw))):
            raise AdjudicationError("Task raw-record checksum mismatch")
    for item in research:
        if not isinstance(item, dict) or type(item.get("row_index")) is not int:
            raise AdjudicationError("Invalid clean research row")
        indexes.add(item["row_index"])
        if (item.get("eligible_for_execution") is not False
                or item.get("eligible_for_ingestion") is not False
                or item.get("official_filing_verified") is not False
                or item.get("first_seen_at") is not None):
            raise AdjudicationError("Unjustified clean-row promotion")
    if len(indexes) != manifest["source_rows"] or indexes != set(range(1, manifest["source_rows"] + 1)):
        raise AdjudicationError("Source row indexes missing or repeated")
    return manifest, sorted(queue, key=lambda x: x["row_index"])


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _resolve_evidence(pdf_dir: Path, review: dict) -> dict:
    filename = review.get("pdf_filename")
    expected = review.get("pdf_sha256")
    if (not isinstance(filename, str) or not filename.endswith(".pdf")
            or Path(filename).name != filename or filename in {"", ".", ".."}):
        raise AdjudicationError("PDF filename must be a basename ending .pdf")
    if not isinstance(expected, str) or not HASH_RE.fullmatch(expected):
        raise AdjudicationError("Invalid expected PDF SHA-256")
    pdf_data = _safe_bytes(pdf_dir / filename)
    if digest(pdf_data) != expected:
        raise AdjudicationError("Provided PDF bytes do not match asserted SHA-256")
    if not pdf_data.startswith(b"%PDF-"):
        raise AdjudicationError("Not a PDF document")
    try:
        reader = PdfReader(BytesIO(pdf_data), strict=True)
        if reader.is_encrypted:
            raise AdjudicationError("Encrypted PDF requires independent review")
        page = review.get("page_number")
        if type(page) is not int or not 1 <= page <= len(reader.pages):
            raise AdjudicationError("PDF page number outside document")
        page_text = reader.pages[page - 1].extract_text()
    except AdjudicationError:
        raise
    except Exception as exc:
        raise AdjudicationError("Unreadable PDF or page text") from exc
    if not isinstance(page_text, str) or not page_text.strip():
        raise AdjudicationError("PDF text unavailable; scanned page remains unresolved")
    excerpt = review.get("transaction_excerpt")
    if not isinstance(excerpt, str) or not 20 <= len(excerpt.strip()) <= 400:
        raise AdjudicationError("Need a 20-400 character verbatim transaction excerpt")
    if _normalized_text(excerpt) not in _normalized_text(page_text):
        raise AdjudicationError("Transaction excerpt not found on asserted PDF page")
    return {
        "pdf_filename": filename,
        "pdf_sha256": expected,
        "page_number": page,
        "transaction_excerpt": excerpt.strip(),
        "excerpt_present_in_pdf_page": True,
        "pdf_origin_authenticated": False,
    }


def _validate_review(review: dict, task: dict, pdf_dir: Path) -> dict:
    if not isinstance(review, dict):
        raise AdjudicationError("Review entry must be an object")
    role = review.get("role")
    reviewer_id = review.get("reviewer_id")
    if role not in ROLES or not isinstance(reviewer_id, str) or not REVIEWER_RE.fullmatch(reviewer_id):
        raise AdjudicationError("Invalid reviewer ID or role")
    for key, allowed in (("action_verdict", ALLOWED_ACTIONS),
                         ("asset_verdict", ALLOWED_ASSETS),
                         ("duplicate_verdict", ALLOWED_DUPLICATE)):
        if review.get(key) not in allowed:
            raise AdjudicationError(f"Unknown {key}")
    if review.get("task_id") != task["task_id"]:
        raise AdjudicationError("Review task ID mismatch")
    # Snapshot, row index and raw fingerprint are NOT editable by reviewers.
    if review.get("row_index") != task["row_index"]:
        raise AdjudicationError("Review row index mismatch")
    timestamp = review.get("reviewed_at_utc")
    if not isinstance(timestamp, str):
        raise AdjudicationError("Reviewer timestamp must be provided")
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            raise ValueError("No timezone")
    except ValueError as exc:
        raise AdjudicationError("Review timestamp needs explicit timezone") from exc
    if dt.utcoffset().total_seconds() != 0:
        raise AdjudicationError("Review timestamp must be UTC")
    if not isinstance(review.get("notes", ""), str) or len(review.get("notes", "")) > 1000:
        raise AdjudicationError("Review notes invalid or oversized")
    evidence = _resolve_evidence(pdf_dir, review)
    excerpt = _normalized_text(evidence["transaction_excerpt"])
    ticker = str(task.get("ticker") or task.get("raw_record", {}).get("ticker") or "").strip().casefold()
    # A page quote without the task's asset identity + transaction date could
    # describe an unrelated row. Require both; otherwise leave it unresolved.
    if not ticker or not re.search(r"(?<![a-z0-9])" + re.escape(ticker) + r"(?![a-z0-9])", excerpt):
        raise AdjudicationError("Transaction excerpt does not identify the task ticker")
    reported = str(task.get("transaction_date") or task.get("raw_record", {}).get("transactionDate") or "")
    try:
        traded = date.fromisoformat(reported)
    except ValueError as exc:
        raise AdjudicationError("Source transaction date invalid") from exc
    date_tokens = {traded.isoformat(), traded.strftime("%m/%d/%Y"),
                   traded.strftime("%m/%d/%y"), traded.strftime("%Y/%m/%d")}
    if not any(token in excerpt for token in date_tokens):
        raise AdjudicationError("Transaction excerpt does not identify source transaction date")
    return {
        "task_id": task["task_id"], "row_index": task["row_index"],
        "raw_record_sha256": task["raw_record_sha256"],
        "role": role, "reviewer_id": reviewer_id,
        "reviewed_at_utc": dt.isoformat(),
        "action_verdict": review["action_verdict"],
        "asset_verdict": review["asset_verdict"],
        "duplicate_verdict": review["duplicate_verdict"],
        "notes": review.get("notes", ""),
        **evidence,
    }


def verify_ledger(events: list[dict], *, genesis_hash: str) -> str:
    """Validate deterministic SHA-256 hash chain; return final digest."""
    if not isinstance(genesis_hash, str) or not HASH_RE.fullmatch(genesis_hash):
        raise AdjudicationError("Invalid genesis hash")
    prior = genesis_hash
    for idx, event in enumerate(events, 1):
        if not isinstance(event, dict) or event.get("sequence") != idx or event.get("previous_hash") != prior:
            raise AdjudicationError("Broken ledger sequence or link")
        body = {k: v for k, v in event.items() if k != "event_hash"}
        if event.get("event_hash") != digest(canonical(body)):
            raise AdjudicationError("Ledger event hash mismatch")
        prior = event["event_hash"]
    return prior


def adjudicate(e118_dir: str | Path, decisions: list[dict], pdf_dir: str | Path) -> dict:
    """Strictly offline decisions; never certify authentic PDFs or release rows."""
    manifest, queue = load_e118(e118_dir)
    if not isinstance(decisions, list) or len(decisions) > 2 * len(queue):
        raise AdjudicationError("Invalid number of reviewer decisions")
    task_by_id = {t["task_id"]: t for t in queue}
    by_task = defaultdict(dict)
    for value in decisions:
        if not isinstance(value, dict) or value.get("task_id") not in task_by_id:
            raise AdjudicationError("Decision references unknown review task")
        task = task_by_id[value["task_id"]]
        entry = _validate_review(value, task, Path(pdf_dir))
        role = entry["role"]
        if role in by_task[entry["task_id"]]:
            raise AdjudicationError("Duplicate reviewer role for a task")
        if any(x["reviewer_id"].casefold() == entry["reviewer_id"].casefold()
               for x in by_task[entry["task_id"]].values()):
            raise AdjudicationError("Two reviewer roles cannot share a reviewer identity")
        by_task[entry["task_id"]][role] = entry
    genome = digest(canonical({"stage": VERSION, "source_manifest_sha256":
                               digest(_safe_bytes(Path(e118_dir) / MANIFEST_FILE)),
                               "source_snapshot_sha256": manifest["source_sha256"]}))
    ledger = []
    previous = genome
    for task in queue:
        for role in ROLES:
            entry = by_task[task["task_id"]].get(role)
            if not entry:
                continue
            body = {"sequence": len(ledger) + 1, "previous_hash": previous,
                    "event_type": "offline_pdf_excerpt_review", "evidence": entry,
                    "source_snapshot_sha256": manifest["source_sha256"],
                    "ingestion_allowed": False, "trading_allowed": False}
            event = {**body, "event_hash": digest(canonical(body))}
            ledger.append(event)
            previous = event["event_hash"]
    if verify_ledger(ledger, genesis_hash=genome) != previous:
        raise AdjudicationError("Ledger validation failed")
    task_status = []
    for task in queue:
        reviews = by_task[task["task_id"]]
        if len(reviews) == 0:
            status = "awaiting_two_reviewers"
        elif len(reviews) == 1:
            status = "awaiting_second_reviewer"
        else:
            one, two = reviews["primary"], reviews["secondary"]
            concordance_fields = ("pdf_sha256", "page_number", "transaction_excerpt",
                                  "action_verdict", "asset_verdict", "duplicate_verdict")
            status = ("two_reviewers_concordant_offline_only"
                      if all(one[k] == two[k] for k in concordance_fields)
                      else "reviewer_disagreement")
        task_status.append({
            "task_id": task["task_id"], "row_index": task["row_index"],
            "review_status": status, "reviewers": [reviews[r]["reviewer_id"] for r in ROLES if r in reviews],
            "semantic_group_id": task.get("semantic_group_id"),
            "source_filing_id": task.get("source_filing_id"),
            "raw_record_sha256": task["raw_record_sha256"],
            "original_house_pdf_authenticated": False,
            "official_filing_verified": False,
            "eligible_for_ingestion": False, "eligible_for_execution": False,
            "first_seen_at": None,
        })
    groups = defaultdict(list)
    for status in task_status:
        if status["semantic_group_id"]:
            groups[status["semantic_group_id"]].append(status)
    return {
        "summary": {
            "version": VERSION, "source_snapshot_sha256": manifest["source_sha256"],
            "review_task_count": len(queue), "review_events": len(ledger),
            "status_counts": dict(sorted(Counter(x["review_status"] for x in task_status).items())),
            "duplicate_groups_pending_evidence": len(groups),
            "source_authenticated_pdf_count": 0,
            "auto_promoted": 0, "verified_trading_signals": 0,
            "ingestion_approved": False, "execution_approved": False,
            "supabase_writes": 0, "broker_orders": 0, "external_fetches": 0,
            "decision": "NO_GO_REQUIRES_AUTHENTICATED_SOURCE_EVIDENCE",
            "genesis_hash": genome, "ledger_head_hash": previous,
        },
        "review_queue": task_status, "ledger": ledger,
    }


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    value = str(value)
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_outputs(result: dict, output_dir: str | Path) -> dict:
    """Write only local audit files, deterministically."""
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {
        "queue": folder / "pelosi_e119_review_status.csv",
        "ledger": folder / "pelosi_e119_audit_ledger.jsonl",
        "template": folder / "pelosi_e119_two_reviewer_template.csv",
        "manifest": folder / "pelosi_e119_manifest.json",
    }
    with paths["queue"].open("w", newline="", encoding="utf-8-sig") as fp:
        cols = ("task_id", "row_index", "review_status", "reviewers", "semantic_group_id",
                "source_filing_id", "raw_record_sha256", "original_house_pdf_authenticated",
                "official_filing_verified", "eligible_for_ingestion", "eligible_for_execution", "first_seen_at")
        writer = csv.DictWriter(fp, fieldnames=cols)
        writer.writeheader()
        for item in result["review_queue"]:
            writer.writerow({k: _csv_cell(item.get(k)) for k in cols})
    with paths["template"].open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for task in result["review_queue"]:
            for role in ROLES:
                writer.writerow({**{k: "" for k in REVIEW_COLUMNS},
                                 "task_id": task["task_id"], "row_index": task["row_index"],
                                 "role": role})
    with paths["ledger"].open("w", encoding="utf-8") as fp:
        for event in result["ledger"]:
            fp.write(canonical(event).decode("utf-8") + "\n")
    hashes = {p.name: digest(_safe_bytes(p, max_size=MAX_FILE_BYTES)) for p in (paths["queue"], paths["template"])}
    # An empty ledger is valid when no House PDFs were provided.
    hashes[paths["ledger"].name] = digest(paths["ledger"].read_bytes())
    output_manifest = {**result["summary"], "output_sha256": dict(sorted(hashes.items()))}
    paths["manifest"].write_text(json.dumps(output_manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"paths": paths, "manifest": output_manifest}


def read_review_csv(path: Path) -> list[dict]:
    """Load the generated two-reviewer CSV, ignoring only truly blank slots.

    A partly filled slot remains a review attempt and must be validated; it
    cannot silently disappear from the audit. All source task IDs/roles remain
    bound to the input row, and the downstream review validator enforces them.
    """
    data = _safe_bytes(path)
    try:
        content = data.decode("utf-8-sig")
        reader = csv.DictReader(content.splitlines())
        if list(reader.fieldnames or []) != list(REVIEW_COLUMNS):
            raise AdjudicationError("Review CSV columns differ from E1.19 template")
        reviews = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise AdjudicationError("Malformed CSV review row")
            # task_id, row_index and role are prefilled in the blank template.
            editable = [key for key in REVIEW_COLUMNS
                        if key not in {"task_id", "row_index", "role"}]
            if all(not str(row.get(key) or "").strip() for key in editable):
                continue
            try:
                row["row_index"] = int(row["row_index"])
                row["page_number"] = int(row["page_number"])
            except (TypeError, ValueError) as exc:
                raise AdjudicationError("Populated CSV review requires integer row/page") from exc
            reviews.append(row)
        return reviews
    except (UnicodeError, csv.Error) as exc:
        raise AdjudicationError("Invalid UTF-8 review CSV") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("e118_directory", type=Path)
    choices = parser.add_mutually_exclusive_group()
    choices.add_argument("--reviews-json", type=Path,
                         help="local JSON file: {reviews:[...]} (optional)")
    choices.add_argument("--reviews-csv", type=Path,
                         help="locally completed two-reviewer CSV template (optional)")
    parser.add_argument("--pdf-dir", type=Path, help="directory of explicitly supplied local PDFs")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.reviews_json:
            data = _read_json(args.reviews_json)
            if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
                raise AdjudicationError("Review JSON requires a reviews array")
            decisions = data["reviews"]
        elif args.reviews_csv:
            decisions = read_review_csv(args.reviews_csv)
        else:
            decisions = []
        if decisions and args.pdf_dir is None:
            raise AdjudicationError("Supplied review evidence requires --pdf-dir")
        result = adjudicate(args.e118_directory, decisions, args.pdf_dir or args.e118_directory)
        report = write_outputs(result, args.output_dir)
    except (AdjudicationError, OSError, ValueError) as exc:
        parser.exit(2, f"NO-GO E1.19: {exc}\n")
    print(json.dumps({"summary": result["summary"], "files": {k: str(v) for k, v in report["paths"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
