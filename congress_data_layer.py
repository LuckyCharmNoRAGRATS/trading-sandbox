"""Read-only Quant AI access to stored politician disclosures.

This module makes no trading decisions and never submits broker orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def filings_asof(db: Any, *, member: str = "Nancy Pelosi",
                 asof: datetime, limit: int = 500) -> list[dict]:
    """Return records whose first system observation was no later than asof.

    Backtests using this API cannot see records before this data collector
    first observed them. Historical raw filings are NOT retroactively tradable.
    """
    if asof.tzinfo is None or asof.utcoffset() is None:
        raise ValueError("asof must include a timezone")
    if not (1 <= limit <= 1000):
        raise ValueError("limit must be between 1 and 1000")
    if not member or not member.strip():
        raise ValueError("member must be specified")
    timestamp = asof.astimezone(timezone.utc).isoformat()
    result = (db.table("politician_disclosures")
              .select("event_id,provider,member_name,chamber,ticker,trade_type,"
                      "asset_description,amount_range,transaction_date,"
                      "filing_date,filing_url,first_seen_at,asset_type,owner_type,upstream_ingested_at")
              .ilike("member_name", f"%{member.strip()}%")
              .lte("first_seen_at", timestamp)
              .order("first_seen_at", desc=False)
              .limit(limit)
              .execute())
    return result.data or []


def disclosure_candidates(db: Any, *, asof: datetime,
                          member: str = "Nancy Pelosi", limit: int = 500) -> list[dict]:
    """Research candidates only; caller must validate options, eligibility, risk."""
    records = filings_asof(db, member=member, asof=asof, limit=limit)
    return [row for row in records
            if row.get("ticker") and row.get("trade_type") in {"buy", "sell"}
            and str(row.get("asset_type") or "").casefold() in {"stock", "common stock", "equity"}]


def connect_from_env() -> Any:
    """Server-side use only. Requires private Supabase service credentials."""
    import os
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_SERVICE_KEY"])
