"""E1.21 Tracefour connection diagnostics, fail-closed and research-only.

The command-line interface NEVER performs HTTP. A single-request probe is
exposed as a function for a separately authorized call with an injected HTTP
session. It never retries, redirects, imports Supabase, or submits orders.
Offline JSON analysis is not evidence of a current network connection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from congress_quality import FeedIntegrityError, parse_timestamp, validate_tracefour_feed

TRACEFOUR_URL = "https://tracefour.com/v1/congress/nancy-pelosi"
MAX_JSON_BYTES = 2_000_000
READ_CHUNK_SIZE = 65_536
REQUEST_TIMEOUT = (10, 20)


class DiagnosticError(ValueError):
    """Offline file or diagnostic contract violates a safety boundary."""


def _safe_time(now: datetime | None) -> datetime:
    return parse_timestamp(now if now is not None else datetime.now(timezone.utc), field="now")


def _base() -> dict[str, Any]:
    return {
        "source_url": TRACEFOUR_URL,
        "approved_for_ingestion": False,
        "approved_for_trading": False,
        "supabase_writes": 0,
        "orders_submitted": 0,
        "scheduled_sync_enabled": False,
    }


def assess_body(body: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Inspect untrusted Tracefour JSON without silently dropping bad records."""
    result = _base()
    result.update({"json_schema_valid": False, "ingestion_ready": False})
    at = _safe_time(now)
    if not isinstance(body, dict):
        result.update({"quality_status": "blocked", "blockers": ["response_not_object"]})
        return result
    data = body.get("data")
    meta = body.get("meta")
    member = data.get("member") if isinstance(data, dict) else None
    trades = data.get("trades") if isinstance(data, dict) else None
    result["member_slug"] = member.get("slug") if isinstance(member, dict) else None
    result["provider_cache_source"] = meta.get("source") if isinstance(meta, dict) else None
    result["meta_count_member_records_not_trades"] = meta.get("count") if isinstance(meta, dict) else None
    rows = trades if isinstance(trades, list) else []
    result["reported_transaction_rows"] = len(rows) if isinstance(trades, list) else None
    result["missing_https_filing_links"] = sum(
        not isinstance(row.get("sourceLink"), str) or not row["sourceLink"].startswith("https://")
        for row in rows if isinstance(row, dict)
    )
    result["malformed_row_objects"] = sum(not isinstance(row, dict) for row in rows)
    result["untrusted_link_hosts"] = sum(
        isinstance(row, dict) and isinstance(row.get("sourceLink"), str)
        and row["sourceLink"].startswith("https://")
        and urlsplit(row["sourceLink"]).hostname != "disclosures-clerk.house.gov"
        for row in rows
    )
    result["unverified_historical_completeness"] = True
    result["official_filings_independently_verified"] = 0
    result["first_seen_at_established"] = False
    cache_at = None
    try:
        cache_at = parse_timestamp(meta.get("fetchedAt") if isinstance(meta, dict) else None,
                                   field="meta.fetchedAt")
        result["cache_fetched_at_utc"] = cache_at.isoformat()
        result["cache_age_seconds"] = round((at - cache_at).total_seconds(), 3)
    except FeedIntegrityError:
        result["cache_fetched_at_utc"] = None
        result["cache_age_seconds"] = None

    blockers = []
    if result["member_slug"] != "nancy-pelosi":
        blockers.append("wrong_or_missing_member")
    if result["reported_transaction_rows"] is None or not (0 < len(rows) < 500):
        blockers.append("missing_empty_or_endpoint_capped_trades")
    if result["malformed_row_objects"]:
        blockers.append("malformed_row_objects")
    if result["missing_https_filing_links"]:
        blockers.append("missing_https_filing_links")
    if result["untrusted_link_hosts"]:
        blockers.append("untrusted_filing_link_hosts")
    if cache_at is None:
        blockers.append("missing_or_naive_cache_timestamp")
    else:
        age = (at - cache_at).total_seconds()
        if age > 6 * 3600:
            blockers.append("stale_provider_cache_over_6h")
        if age < -5 * 60:
            blockers.append("future_provider_cache_timestamp")
    try:
        validated = validate_tracefour_feed(body, now=at)
        result["json_schema_valid"] = True
        result["strict_validator_rows"] = validated["record_count"]
    except (FeedIntegrityError, ValueError, TypeError) as exc:
        result["strict_validator_error"] = str(exc)
        blockers.append("strict_tracefour_contract_failed")
    if not blockers:
        # Even a clean data envelope does not authenticate original filings or
        # establish first-observed publication times / complete history.
        result["quality_status"] = "structurally_valid_source_unverified"
        result["blockers"] = ["original_filing_authenticity_unverified",
                              "historical_completeness_unverified",
                              "first_seen_at_unavailable"]
    else:
        result["quality_status"] = "blocked"
        result["blockers"] = sorted(set(blockers))
    return result


def diagnose_saved_json(path: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Read one local file, never a URL; no network or database side effects."""
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise DiagnosticError("Expected a regular local JSON file, not a URL or symlink")
    size = source.stat().st_size
    if not 0 < size <= MAX_JSON_BYTES:
        raise DiagnosticError("Saved response is empty or exceeds size limit")
    raw = source.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise DiagnosticError("Saved response changed size during read")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise DiagnosticError("Saved response is not UTF-8 JSON") from exc
    result = assess_body(body, now=now)
    result.update({
        "mode": "offline_saved_response",
        "transport_status": "not_tested_by_saved_file",
        "http_status": None,
        "live_response_verified": False,
        "source_file_name": source.name,
        "source_file_sha256": hashlib.sha256(raw).hexdigest(),
        "source_file_bytes": len(raw),
        "outbound_http_requests": 0,
    })
    return result


def _response_bytes(response: Any) -> bytes:
    out = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=READ_CHUNK_SIZE):
            if not isinstance(chunk, bytes):
                raise DiagnosticError("HTTP response chunk is not bytes")
            out.extend(chunk)
            if len(out) > MAX_JSON_BYTES:
                raise DiagnosticError("HTTP body exceeds safe size limit")
    except DiagnosticError:
        raise
    except Exception as exc:
        # Never claim a complete response when a stream fails mid-transfer.
        raise DiagnosticError("Failed to read complete HTTP response") from exc
    return bytes(out)


def probe_once(session: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Perform AT MOST ONE GET via an injected session. NO retries/redirects.

    The CLI intentionally does not expose this function. Calling it on a real
    session requires separate, explicit approval for that particular request.
    """
    at = _safe_time(now)
    result = _base()
    result.update({"mode": "single_request_probe", "http_status": None,
                   "live_response_verified": False, "ingestion_ready": False,
                   "outbound_http_requests_attempted": 1})
    try:
        response = session.get(TRACEFOUR_URL, timeout=REQUEST_TIMEOUT,
                               allow_redirects=False, stream=True,
                               headers={"Accept": "application/json"})
    except Exception as exc:
        # One request was attempted but no provider HTTP response is known.
        result.update({"transport_status": "failed_before_http_response",
                       "error_type": type(exc).__name__,
                       "http_status": None,
                       "blockers": ["http_response_unavailable"]})
        return result
    try:
        status = getattr(response, "status_code", None)
        result["http_status"] = status if isinstance(status, int) else None
        if not isinstance(status, int):
            result.update({"transport_status": "invalid_http_response",
                           "blockers": ["missing_http_status"]})
            return result
        if 300 <= status < 400:
            result.update({"transport_status": "redirect_blocked",
                           "blockers": ["redirect_not_followed"]})
            return result
        if not 200 <= status < 300:
            result.update({"transport_status": "http_error",
                           "blockers": ["http_non_2xx"]})
            return result
        content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type != "application/json" and not content_type.endswith("+json"):
            result.update({"transport_status": "unsupported_content_type",
                           "content_type": content_type,
                           "blockers": ["response_not_json_content_type"]})
            return result
        try:
            raw = _response_bytes(response)
            body = json.loads(raw.decode("utf-8"))
        except (DiagnosticError, UnicodeError, ValueError) as exc:
            result.update({"transport_status": "invalid_body",
                           "blockers": ["body_oversized_incomplete_or_not_json"],
                           "error_type": type(exc).__name__})
            return result
        result.update(assess_body(body, now=at))
        result.update({"transport_status": "http_json_received",
                       "http_status": status,
                       "live_response_verified": True,
                       "response_bytes": len(raw),
                       "response_sha256": hashlib.sha256(raw).hexdigest()})
        # Data quality status is independent of network connection success.
        return result
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Tracefour snapshot connection/quality diagnostic; no HTTP")
    parser.add_argument("json_file", type=Path, help="saved JSON path only (no URL)")
    args = parser.parse_args(argv)
    try:
        report = diagnose_saved_json(args.json_file)
    except DiagnosticError as exc:
        parser.exit(2, f"DIAGNOSTIC FAILED: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["quality_status"] != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
