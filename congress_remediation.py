"""E1.17 deterministic, entirely offline quarantine of Pelosi disclosure snapshots.

A record that passes *structural* screening is NOT an authenticated filing,
verified equity transaction, point-in-time signal, or approval for ingestion.
No requests, Supabase, broker SDK, or repository imports are used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import re
import math
from urllib.parse import urlsplit

PROVIDER = "tracefour"
SOURCE_ENDPOINT = "https://tracefour.com/v1/congress/nancy-pelosi"
MEMBER_SLUG = "nancy-pelosi"
MAX_ROWS = 499  # 500-row endpoint boundary cannot prove completeness
MAX_INPUT_BYTES = 10_000_000
ALLOWED_ASSET_TYPES = {"Stock", "Stock Option"}
TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,15}\Z")
DERIVATIVE_DESCRIPTION = re.compile(r"\b(?:calls?|puts?|options?|leaps?|exercis(?:e|es|ed|ing))\b", re.IGNORECASE)
OFFICIAL_PDF_PATH = re.compile(r"/public_disc/ptr-pdfs/\d{4}/\d+\.pdf\Z")
SOURCE_SALE_ID = re.compile(r"\|Sale(?: \([^|]*\))?\|", re.IGNORECASE)
ISO_TZ = re.compile(r"(?:Z|[+-]\d{2}:\d{2})\Z")
BASE_COLUMNS = [
    "row_index", "research_event_id", "ticker", "member_name", "member_slug",
    "owner_type", "asset_type_reported", "asset_description", "action_reported",
    "transaction_date", "filing_date", "amount_label", "amount_min_usd",
    "amount_max_usd", "source_filing_id", "official_filing_url",
    "upstream_ingested_at_raw", "upstream_ingested_at_utc", "first_seen_at",
    "provider", "provider_endpoint", "provider_cache_fetched_at_utc",
    "attribution_url", "license_url", "snapshot_sha256", "raw_record_sha256",
    "semantic_group_id", "semantic_group_size", "quality_status", "reason_codes",
    "eligible_for_execution", "eligible_for_ingestion", "official_filing_verified",
]


class RemediationError(ValueError):
    """Invalid snapshot or unsafe output; no partial research result is approved."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_obj(value: object) -> str:
    return _sha_bytes(_json(value).encode("utf-8"))


def _read_snapshot(path: Path):
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
        raise RemediationError("Snapshot missing, empty or beyond size limit")
    raw = path.read_bytes()
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RemediationError("Snapshot must be UTF-8 JSON") from exc
    if not isinstance(body, dict) or body.get("error") not in (None, False):
        raise RemediationError("Untrusted or failed source envelope")
    data, meta = body.get("data"), body.get("meta")
    if not isinstance(data, dict) or not isinstance(meta, dict):
        raise RemediationError("Missing member or provider metadata")
    member = data.get("member")
    if not isinstance(member, dict) or member.get("slug") != MEMBER_SLUG:
        raise RemediationError("Incorrect disclosure member")
    rows = data.get("trades")
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS:
        raise RemediationError("Empty or potentially truncated disclosure collection")
    if any(not isinstance(row, dict) for row in rows):
        raise RemediationError("Malformed disclosure record")
    cache_time = _timestamp(meta.get("fetchedAt"))
    if cache_time is None:
        raise RemediationError("Provider cache timestamp is missing or timezone-naive")
    # Historical replay does NOT substitute a timestamp from the snapshot for
    # first_seen_at. Freshness is reported, not silently used as authorization.
    attribution = meta.get("attribution")
    if not isinstance(attribution, dict):
        raise RemediationError("Provider attribution metadata missing")
    page = attribution.get("page")
    license_url = meta.get("license")
    if not _secure_web_url(page) or not _secure_web_url(license_url):
        raise RemediationError("Source attribution or license URL invalid")
    return body, raw, cache_time, page, license_url


def _timestamp(value):
    if not isinstance(value, str) or not ISO_TZ.search(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _secure_web_url(url):
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password


def _official_pdf_url(url):
    if not _secure_web_url(url):
        return False
    parts = urlsplit(url)
    try:
        return (parts.hostname == "disclosures-clerk.house.gov" and
                parts.port in (None, 443) and not parts.query and not parts.fragment and
                bool(OFFICIAL_PDF_PATH.fullmatch(parts.path)))
    except ValueError:
        return False


def _dates(row, reasons, *, asof_date):
    raw_tx, raw_filing = row.get("transactionDate"), row.get("disclosureDate")
    try:
        tx = date.fromisoformat(raw_tx) if isinstance(raw_tx, str) else None
        filing = date.fromisoformat(raw_filing) if isinstance(raw_filing, str) else None
    except ValueError:
        tx, filing = None, None
    if tx is None or filing is None or filing < tx:
        reasons.add("invalid_or_inverted_dates")
    elif tx > asof_date or filing > asof_date:
        reasons.add("future_transaction_or_filing_date")
    return tx, filing


def _amount(row, reasons):
    low, high, mid = row.get("amountMin"), row.get("amountMax"), row.get("amountMid")
    if (any(isinstance(x, bool) or not isinstance(x, (int, float))
            or not math.isfinite(x) for x in (low, high, mid))
            or not (0 <= low <= mid <= high)):
        reasons.add("invalid_amount_bounds")
    return low, high


def _semantic_key(row):
    # Deliberately excludes filing date/URL and provider ID; these are precisely
    # where repeated filings or rewritten source records may disagree.
    return tuple(str(row.get(field) or "").strip().casefold() for field in (
        "ticker", "transactionDate", "type", "owner", "amountLabel", "assetType"))


def _event_id(source_id):
    # Same deterministic source ID mapping as congress_ingest.normalize_trade.
    return _sha_obj({"provider": PROVIDER, "source_id": source_id})


def remediate_snapshot(input_path: str | Path, *, assessed_at: datetime | None = None) -> dict:
    """Return fully partitioned records; never calls a service or writes files.

    The provided timestamp is analysis metadata only, not system first_seen_at.
    No row is ever promoted to trading/ingestion ready based on this function.
    """
    path = Path(input_path)
    body, raw, cache_time, attribution_url, license_url = _read_snapshot(path)
    at = assessed_at or datetime.now(timezone.utc)
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise RemediationError("Assessment timestamp must be timezone aware")
    at = at.astimezone(timezone.utc)
    snapshot_sha = _sha_bytes(raw)
    rows = body["data"]["trades"]
    groups = defaultdict(list)
    source_ids = defaultdict(list)
    for i, row in enumerate(rows, 1):
        groups[_semantic_key(row)].append(i)
        source_ids[str(row.get("filingId") or "")].append(i)
    clean, quarantine = [], []
    reason_counts = Counter()
    semantic_dupe_groups = set()
    for index, raw_row in enumerate(rows, 1):
        reasons = set()
        sid = raw_row.get("filingId")
        if not isinstance(sid, str) or not sid.strip():
            reasons.add("missing_source_filing_id")
        if len(source_ids[str(sid or "")]) > 1:
            reasons.add("duplicate_source_filing_id")
        if raw_row.get("memberSlug") != MEMBER_SLUG or str(raw_row.get("memberName") or "").strip().casefold() != "nancy pelosi":
            reasons.add("member_identity_mismatch")
        ticker = raw_row.get("ticker")
        if not isinstance(ticker, str) or not TICKER.fullmatch(ticker):
            reasons.add("invalid_ticker")
        asset_type = raw_row.get("assetType")
        if asset_type not in ALLOWED_ASSET_TYPES:
            reasons.add("unknown_asset_type")
        desc = str(raw_row.get("assetDescription") or "")
        if asset_type == "Stock" and DERIVATIVE_DESCRIPTION.search(desc):
            reasons.add("stock_label_derivative_or_exercise_description")
        action = raw_row.get("type")
        if action == "Other":
            reasons.add("ambiguous_action_other")
            if isinstance(sid, str) and SOURCE_SALE_ID.search(sid):
                reasons.add("source_id_suggests_sale_but_type_other")
        elif action not in {"Purchase", "Sale", "Exchange"}:
            reasons.add("unknown_action_type")
        url = raw_row.get("sourceLink")
        if not _official_pdf_url(url):
            reasons.add("missing_or_untrusted_official_filing_url")
        upstream = _timestamp(raw_row.get("ingestedAt"))
        if upstream is None:
            reasons.add("upstream_timestamp_missing_or_timezone_naive")
        tx, filing = _dates(raw_row, reasons, asof_date=at.date())
        low, high = _amount(raw_row, reasons)
        group_index = groups[_semantic_key(raw_row)]
        group_id = _sha_obj({"provider": PROVIDER, "semantics": _semantic_key(raw_row)})[:16] if len(group_index) > 1 else ""
        if group_id:
            reasons.add("potential_semantic_duplicate")
            semantic_dupe_groups.add(group_id)
        for r in reasons:
            reason_counts[r] += 1
        item = {
            "row_index": index,
            "research_event_id": _event_id(sid) if isinstance(sid, str) and sid else None,
            "ticker": ticker,
            "member_name": raw_row.get("memberName"),
            "member_slug": raw_row.get("memberSlug"),
            "owner_type": raw_row.get("owner"),
            "asset_type_reported": asset_type,
            "asset_description": raw_row.get("assetDescription"),
            "action_reported": action,
            "transaction_date": tx.isoformat() if tx is not None else raw_row.get("transactionDate"),
            "filing_date": filing.isoformat() if filing is not None else raw_row.get("disclosureDate"),
            "amount_label": raw_row.get("amountLabel"),
            "amount_min_usd": low,
            "amount_max_usd": high,
            "source_filing_id": sid,
            "official_filing_url": url,
            "upstream_ingested_at_raw": raw_row.get("ingestedAt"),
            "upstream_ingested_at_utc": upstream,
            "first_seen_at": None,  # Never retroactively invent observation time.
            "provider": PROVIDER,
            "provider_endpoint": SOURCE_ENDPOINT,
            "provider_cache_fetched_at_utc": cache_time,
            "attribution_url": attribution_url,
            "license_url": license_url,
            "snapshot_sha256": snapshot_sha,
            "raw_record_sha256": _sha_obj(raw_row),
            "semantic_group_id": group_id or None,
            "semantic_group_size": len(group_index),
            "quality_status": ("quarantined_review_required" if reasons else "structurally_clean_source_unverified"),
            "reason_codes": sorted(reasons),
            "eligible_for_execution": False,
            "eligible_for_ingestion": False,
            "official_filing_verified": False,
            "raw_record": raw_row,  # Full immutable source row in JSONL.
        }
        (quarantine if reasons else clean).append(item)
    if len(clean) + len(quarantine) != len(rows):
        raise RemediationError("Record partition invariant failed")
    if {id(row) for row in clean}.intersection({id(row) for row in quarantine}):
        raise RemediationError("Overlapping record partition")
    return {
        "summary": {
            "status": "NO_GO_RESEARCH_ONLY_SOURCE_UNVERIFIED",
            "assessment_utc": at.isoformat(),
            "source_file_name": path.name,
            "source_file_bytes": len(raw),
            "source_snapshot_sha256": snapshot_sha,
            "provider": PROVIDER,
            "provider_endpoint": SOURCE_ENDPOINT,
            "provider_cache_fetched_at_utc": cache_time,
            "member": MEMBER_SLUG,
            "provider_meta_count_is_member_count": body["meta"].get("count"),
            "attribution_url": attribution_url,
            "license_url": license_url,
            "total_rows": len(rows),
            "structurally_clean_unverified_rows": len(clean),
            "quarantined_rows": len(quarantine),
            "quarantine_reason_counts_overlapping": dict(sorted(reason_counts.items())),
            "potential_semantic_duplicate_groups": len(semantic_dupe_groups),
            "official_filing_links_independently_checked": False,
            "source_download_authenticated_independently": False,
            "historical_first_seen_at_known": False,
            "provider_history_complete_independently": False,
            "research_only": True,
            "ingestion_approved": False,
            "trading_approved": False,
            "external_api_calls": 0,
            "supabase_writes": 0,
            "orders_submitted": 0,
        },
        "clean": clean,
        "quarantine": quarantine,
    }


def _csv_safe(value):
    """Prevent spreadsheet CSV formula injection in user-supplied free text."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = _json(value)
    result = str(value)
    if result and (result[0] in "=+-@\t\r\n" or result.lstrip()[:1] in "=+-@"):
        return "'" + result
    return result


def write_research_outputs(result: dict, output_dir: str | Path) -> dict:
    """Write reproducible local CSV/JSONL+manifest only, never a database."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filenames = {
        "clean_csv": "pelosi_e117_clean_research.csv",
        "quarantine_csv": "pelosi_e117_quarantined_review.csv",
        "clean_jsonl": "pelosi_e117_clean_research.jsonl",
        "quarantine_jsonl": "pelosi_e117_quarantined_review.jsonl",
        "summary": "pelosi_e117_summary.json",
    }
    paths = {key: directory / name for key, name in filenames.items()}
    for status, prefix in (("clean", "clean"), ("quarantine", "quarantine")):
        records = result[status]
        # CSV is a convenience view, not a replacement for raw JSONL provenance.
        with paths[f"{prefix}_csv"].open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=BASE_COLUMNS)
            writer.writeheader()
            for item in records:
                writer.writerow({name: _csv_safe(item.get(name)) for name in BASE_COLUMNS})
        with paths[f"{prefix}_jsonl"].open("w", encoding="utf-8") as stream:
            for item in records:
                stream.write(_json(item) + "\n")
    manifest = {
        **result["summary"],
        "output_files_sha256": {
            filenames[key]: _sha_bytes(path.read_bytes())
            for key, path in paths.items() if key != "summary"
        },
        "output_file_rows": {
            filenames[key]: len(result["clean"] if key.startswith("clean") else result["quarantine"])
            for key in ("clean_csv", "clean_jsonl", "quarantine_csv", "quarantine_jsonl")
        },
    }
    paths["summary"].write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"manifest": manifest, "paths": paths}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_json", type=Path, help="pre-downloaded local JSON only")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = remediate_snapshot(args.snapshot_json)
        written = write_research_outputs(result, args.output_dir)
    except (RemediationError, ValueError, OSError) as exc:
        parser.exit(2, f"QUARANTINED: {exc}\n")
    print(json.dumps({"summary": result["summary"],
                      "output_files": {k: str(v) for k, v in written["paths"].items()}},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
