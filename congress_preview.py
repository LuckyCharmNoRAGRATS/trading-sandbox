"""Validate an already-downloaded Tracefour member JSON file, strictly offline.

E1.16 Approval A continuation. This tool NEVER downloads, imports a broker,
opens a database, or activates a workflow. File integrity, not network identity,
is all it can establish. The caller is responsible for the file's origin.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median

from congress_ingest import normalize_trade
from congress_quality import FeedIntegrityError, validate_tracefour_feed

MAX_SNAPSHOT_BYTES = 10_000_000
SOURCE_URL = "https://tracefour.com/v1/congress/nancy-pelosi"


class PreviewError(RuntimeError):
    """File cannot safely be reported as a valid disclosure preview."""


def analyze_file(path: str | Path, *, now: datetime | None = None,
                 sample_limit: int = 3) -> dict:
    """Analyze only local bytes. `now` injection is for deterministic tests.

    Any invalid trade rejects the entire file; never report partial evidence
    as complete. `meta.fetchedAt` freshness is checked against real UTC by
    default, NOT against its own timestamp like a historical fixture test.
    """
    if isinstance(sample_limit, bool) or not isinstance(sample_limit, int) or not 0 <= sample_limit <= 10:
        raise ValueError("sample_limit must be 0..10")
    file_path = Path(path)
    if not file_path.is_file():
        raise PreviewError("JSON file does not exist")
    size = file_path.stat().st_size
    if not 0 < size <= MAX_SNAPSHOT_BYTES:
        raise PreviewError("Empty or oversized JSON snapshot")
    try:
        raw = file_path.read_text(encoding="utf-8")
        body = json.loads(raw)
    except (UnicodeError, ValueError, OSError) as exc:
        raise PreviewError("Snapshot is not valid UTF-8 JSON") from exc
    try:
        validated = validate_tracefour_feed(body, now=now)
    except (FeedIntegrityError, ValueError, TypeError) as exc:
        raise PreviewError(f"Tracefour contract failed: {exc}") from exc

    normalized = []
    filing_ids = set()
    event_ids = set()
    for row in validated["trades"]:
        filing_id = row["filingId"]
        if filing_id in filing_ids:
            raise PreviewError("Duplicate source filing identifier")
        filing_ids.add(filing_id)
        try:
            item = normalize_trade(row, provider="tracefour")
        except (ValueError, TypeError) as exc:
            raise PreviewError(f"Invalid dated disclosure: {exc}") from exc
        if item["event_id"] in event_ids:
            raise PreviewError("Duplicate normalized disclosure event")
        event_ids.add(item["event_id"])
        normalized.append(item)

    asset_counts = Counter((item["asset_type"] or "unknown").strip().casefold() for item in normalized)
    trade_counts = Counter(item["trade_type"] for item in normalized)
    tickers = sorted({item["ticker"] for item in normalized if item["ticker"]})
    lags = [(datetime.fromisoformat(x["filing_date"]).date() -
             datetime.fromisoformat(x["transaction_date"]).date()).days for x in normalized]
    filings = sorted(item["filing_date"] for item in normalized)
    display = sorted(normalized, key=lambda x: (x["filing_date"], x["event_id"]), reverse=True)
    sample = [{
        "ticker": x["ticker"],
        "trade_type": x["trade_type"],
        "asset_type": x["asset_type"],
        "owner_type": x["owner_type"],
        "transaction_date": x["transaction_date"],
        "filing_date": x["filing_date"],
        "filing_url": x["filing_url"],
    } for x in display[:sample_limit]]

    return {
        "status": "offline_file_validated_only",
        "requested_source_url": SOURCE_URL,
        "source_file": file_path.name,
        "source_file_bytes": size,
        "provider_cache_fetched_at": validated["cache_fetched_at"],
        "cache_age_seconds": validated["cache_age_seconds"],
        "disclosure_count": len(normalized),
        "unique_filing_ids": len(filing_ids),
        "asset_type_counts": dict(sorted(asset_counts.items())),
        "trade_type_counts": dict(sorted(trade_counts.items())),
        "distinct_tickers": len(tickers),
        "latest_filing_date": filings[-1],
        "median_disclosure_lag_days": median(lags),
        "max_disclosure_lag_days": max(lags),
        "sample": sample,
        "warnings": [
            "A fresh provider cache does not mean new filings or tradable real-time signals.",
            "Historical completeness and original filing content remain independently unverified.",
            "Validating local JSON does not authenticate its download origin.",
        ],
        "live_http_requests": 0,
        "supabase_writes": 0,
        "broker_orders": 0,
        "ingestion_approved": False,
        "trading_approved": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path, help="saved Tracefour response JSON; no URL")
    parser.add_argument("--sample", type=int, default=3,
                        help="number of example filings to summarize, 0..10")
    args = parser.parse_args(argv)
    try:
        report = analyze_file(args.json_file, sample_limit=args.sample)
    except (PreviewError, ValueError) as exc:
        parser.exit(1, f"NOT VERIFIED: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
