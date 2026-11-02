"""Read-only Quant AI access to stored politician disclosures.

Research-only. Requires an injected authorized database client; an optional
server-side connector is provided for existing workflows only. Does not trade.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from congress_research_guard import read_filings_asof, evaluate_disclosures


def filings_asof(db: Any, *, member: str = "Nancy Pelosi",
                 asof: datetime, limit: int = 500) -> list[dict]:
    """Bounded, point-in-time data with explicit pagination failure."""
    return read_filings_asof(db, member=member, asof=asof, limit=limit)


def disclosure_candidates(db: Any, *, asof: datetime,
                          member: str = "Nancy Pelosi", limit: int = 500) -> list[dict]:
    """Projected eligible disclosed equities, never actionable orders."""
    rows = filings_asof(db, asof=asof, member=member, limit=limit)
    return evaluate_disclosures(rows, asof=asof, member=member)["candidates"]


def connect_from_env() -> Any:
    """Server-side only. Service credentials must not reach a browser."""
    import os
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_SERVICE_KEY"])
