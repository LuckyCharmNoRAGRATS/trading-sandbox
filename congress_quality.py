"""Offline quality gates for publicly filed congressional disclosures.

No imports of HTTP clients, broker SDKs, or database connectors.
Provider cache freshness is NOT the same as trade publication timeliness.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class FeedIntegrityError(RuntimeError):
    """A missing, stale, truncated or contradictory feed must not be synced."""


TRACEFOUR_MAX_TRADES = 500
MAX_CACHE_AGE = timedelta(hours=6)
MAX_CLOCK_SKEW = timedelta(minutes=5)


def parse_timestamp(value: Any, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FeedIntegrityError(f"Invalid {field} timestamp") from exc
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise FeedIntegrityError(f"Missing timezone-aware {field} timestamp")
    return value.astimezone(timezone.utc)


def validate_tracefour_feed(body: Any, *, now: datetime | None = None,
                            max_cache_age: timedelta = MAX_CACHE_AGE) -> dict:
    """Validate an injected /v1/congress/nancy-pelosi response (no HTTP).

    Tracefour's `meta.count` is the number of MEMBER records (normally 1),
    not a trade count. The 500-result member response is an ambiguous cap.
    """
    at = parse_timestamp(now or datetime.now(timezone.utc), field="now")
    if not isinstance(max_cache_age, timedelta) or max_cache_age <= timedelta(0):
        raise ValueError("max_cache_age must be positive")
    if not isinstance(body, dict) or body.get("error") not in (None, False):
        raise FeedIntegrityError("Source returned an error or malformed envelope")
    data, meta = body.get("data"), body.get("meta")
    if not isinstance(data, dict) or not isinstance(meta, dict):
        raise FeedIntegrityError("Missing Tracefour data/meta envelope")
    member = data.get("member")
    if not isinstance(member, dict) or member.get("slug") != "nancy-pelosi":
        raise FeedIntegrityError("Unexpected member identity")
    source_time = parse_timestamp(meta.get("fetchedAt"), field="meta.fetchedAt")
    age = at - source_time
    if age < -MAX_CLOCK_SKEW:
        raise FeedIntegrityError("Source cache timestamp lies in the future")
    if age > max_cache_age:
        raise FeedIntegrityError("Source cache is stale")
    if not isinstance(meta.get("source"), str) or not meta["source"].strip():
        raise FeedIntegrityError("Missing source provenance")
    rows = data.get("trades")
    if not isinstance(rows, list) or not rows:
        raise FeedIntegrityError("Empty or malformed member trade feed")
    if len(rows) >= TRACEFOUR_MAX_TRADES:
        raise FeedIntegrityError("500-record boundary: historical completeness unproven")
    if any(not isinstance(row, dict) for row in rows):
        raise FeedIntegrityError("Malformed trade item")
    # A nested member ID, source link and stable row ID are present in Tracefour's
    # documented contract; reject data that cannot be independently traced.
    for row in rows:
        if row.get("memberSlug") not in (None, "nancy-pelosi"):
            raise FeedIntegrityError("Trade belongs to a different member")
        if str(row.get("memberName") or "").strip().casefold() != "nancy pelosi":
            raise FeedIntegrityError("Trade member name does not match")
        if not isinstance(row.get("filingId"), str) or not row["filingId"].strip():
            raise FeedIntegrityError("Missing stable source filing identifier")
        link = row.get("sourceLink")
        if not isinstance(link, str) or not link.startswith("https://"):
            raise FeedIntegrityError("Missing HTTPS source filing link")
    return {
        "provider": "tracefour",
        "member": "nancy-pelosi",
        "cache_fetched_at": source_time.isoformat(),
        "cache_age_seconds": max(0.0, age.total_seconds()),
        "trades": rows,
        "record_count": len(rows),
        "complete_within_endpoint_cap": True,
        "source_attribution_required": True,
        "trading_authorized": False,
    }


def assess_sync_health(runs: list[dict], *, now: datetime | None = None,
                       max_success_age: timedelta = timedelta(hours=3)) -> dict:
    """Pure monitoring check over injected historical sync-run records."""
    at = parse_timestamp(now or datetime.now(timezone.utc), field="now")
    if not isinstance(runs, list) or any(not isinstance(r, dict) for r in runs):
        raise FeedIntegrityError("Invalid sync-run collection")
    if max_success_age <= timedelta(0):
        raise ValueError("max_success_age must be positive")
    successful = [r for r in runs if r.get("status") == "success"]
    if not successful:
        return {"ready": False, "reason": "no_successful_sync", "last_success": None}
    last = max(successful, key=lambda r: parse_timestamp(r.get("finished_at"), field="finished_at"))
    finished = parse_timestamp(last.get("finished_at"), field="finished_at")
    age = at - finished
    if age < -MAX_CLOCK_SKEW:
        raise FeedIntegrityError("Successful sync is future-dated")
    try:
        fetched = int(last.get("fetched"))
        accepted = int(last.get("accepted"))
    except (ValueError, TypeError) as exc:
        raise FeedIntegrityError("Unverified sync counts") from exc
    if fetched <= 0 or accepted <= 0 or accepted > fetched:
        return {"ready": False, "reason": "unverified_sync_counts",
                "last_success": finished.isoformat()}
    return {
        "ready": age <= max_success_age,
        "reason": "healthy" if age <= max_success_age else "sync_stale",
        "last_success": finished.isoformat(),
        "last_fetched": fetched,
        "last_accepted": accepted,
    }
