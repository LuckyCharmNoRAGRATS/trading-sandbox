"""E1.15 offline-only congressional disclosure research gate.

This deliberately does not import Supabase, requests, or a trading SDK.
Caller injects an already authorized read-only database handle. Source data
are delayed PUBLIC disclosures, not order instructions or real-time signals.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Any


class DisclosureIntegrityError(RuntimeError):
    """Insufficient or contradictory evidence: fail closed for research."""


EQUITY_TYPES = frozenset({"stock", "common stock", "equity"})
TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,15}\Z")
EVENT_ID = re.compile(r"[a-f0-9]{64}\Z")
# Tracefour member endpoint states it returns up to 500 trades; 500 is an
# ambiguous full page, not proof of complete historical coverage.
TRACEFOUR_MEMBER_LIMIT = 500


def _aware_utc(value: Any, *, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DisclosureIntegrityError(f"Invalid {name}") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DisclosureIntegrityError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def validate_tracefour_member_envelope(body: Any) -> list[dict]:
    """Validate an injected Tracefour response; no HTTP is performed."""
    if not isinstance(body, dict) or body.get("error") not in (None, False):
        raise DisclosureIntegrityError("Source returned an error or malformed envelope")
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("member"), dict):
        raise DisclosureIntegrityError("Missing member identity")
    if data["member"].get("slug") != "nancy-pelosi":
        raise DisclosureIntegrityError("Unexpected congressional member")
    rows = data.get("trades")
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
        raise DisclosureIntegrityError("Malformed congressional transactions")
    if len(rows) >= TRACEFOUR_MEMBER_LIMIT:
        raise DisclosureIntegrityError("Member endpoint at 500-record limit: history may be truncated")
    return rows


def read_filings_asof(db: Any, *, asof: datetime, member: str = "Nancy Pelosi",
                      limit: int = 500) -> list[dict]:
    """Read bounded point-in-time disclosures; refuses ambiguous truncation.

    Database first_seen_at records when OUR collector first observed a filing,
    not when a politician transacted or when the official source published.
    """
    cutoff = _aware_utc(asof, name="asof")
    if not isinstance(member, str) or not member.strip():
        raise ValueError("member is required")
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be 1..500")
    response = (db.table("politician_disclosures")
                .select("event_id,provider,member_name,chamber,ticker,trade_type,"
                        "asset_description,amount_range,transaction_date,"
                        "filing_date,filing_url,first_seen_at,asset_type,owner_type,"
                        "upstream_ingested_at")
                .ilike("member_name", f"%{member.strip()}%")
                .lte("first_seen_at", cutoff.isoformat())
                .order("first_seen_at", desc=True)
                .limit(limit + 1).execute())
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DisclosureIntegrityError("Database disclosure response incomplete")
    if len(rows) > limit:
        raise DisclosureIntegrityError("Disclosure query at limit: pagination required")
    return rows


def evaluate_disclosures(rows: list[dict], *, asof: datetime,
                         member: str = "Nancy Pelosi") -> dict:
    """Classify disclosed *equities* for research without issuing trade signals.

    Unknown asset classes (including options), missing source links, future
    observations and malformed histories cannot become equity candidates.
    """
    cutoff = _aware_utc(asof, name="asof")
    if not isinstance(rows, list) or len(rows) > 500:
        raise DisclosureIntegrityError("Disclosure collection missing or unbounded")
    seen = set()
    candidates = []
    excluded = 0
    for row in rows:
        if not isinstance(row, dict):
            raise DisclosureIntegrityError("Disclosure row is not an object")
        uid = row.get("event_id")
        if not isinstance(uid, str) or not EVENT_ID.fullmatch(uid) or uid in seen:
            raise DisclosureIntegrityError("Invalid or repeated event identity")
        seen.add(uid)
        if str(row.get("member_name") or "").casefold().strip() != member.casefold().strip():
            raise DisclosureIntegrityError("Wrong member in research feed")
        observed = _aware_utc(row.get("first_seen_at"), name="first_seen_at")
        if observed > cutoff:
            raise DisclosureIntegrityError("Future observation leaked into backtest")
        try:
            traded = date.fromisoformat(str(row["transaction_date"]))
            filed = date.fromisoformat(str(row["filing_date"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise DisclosureIntegrityError("Invalid disclosure date") from exc
        if filed < traded or filed > observed.date():
            raise DisclosureIntegrityError("Contradictory transaction, filing or observation dates")
        asset = str(row.get("asset_type") or "").strip().casefold()
        action = str(row.get("trade_type") or "").strip().casefold()
        ticker = str(row.get("ticker") or "").strip().upper()
        link = row.get("filing_url")
        if (asset not in EQUITY_TYPES or action not in {"buy", "sell"}
                or not TICKER.fullmatch(ticker)
                or not isinstance(link, str) or not link.startswith("https://")):
            excluded += 1
            continue
        candidates.append({
            "event_id": uid,
            "ticker": ticker,
            "disclosed_action": action,
            "owner_type": row.get("owner_type"),
            "amount_range": row.get("amount_range"),
            "transaction_date": traded.isoformat(),
            "filing_date": filed.isoformat(),
            "first_seen_at": observed.isoformat(),
            "reporting_lag_days": (filed - traded).days,
            "source_filing_url": link,
        })
    candidates.sort(key=lambda item: (item["first_seen_at"], item["event_id"]))
    return {
        "asof_utc": cutoff.isoformat(),
        "member": member,
        "observed_records": len(rows),
        "eligible_equity_disclosures": len(candidates),
        "excluded_non_equity_or_unverified": excluded,
        "candidates": candidates,
        "order_submission_authorized": False,
        "disclosure_is_live_trade": False,
    }
