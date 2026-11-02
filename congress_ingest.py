"""Poll publicly filed congressional transactions into Supabase.

Data-only connector: does not import or call any broker API.
Source documentation: https://congressinfor-production.up.railway.app/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger("congress_ingest")
DEFAULT_API = "https://congressinfor-production.up.railway.app"
PROVIDER = "congressinvests"


class SourceError(RuntimeError):
    """Invalid or unavailable source response."""


def normalize_trade(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("trade must be an object")
    member = str(row.get("member") or row.get("politician") or "").strip()
    tx_date = str(row.get("tx_date") or row.get("transaction_date") or "").strip()
    disclosed = str(row.get("disclosed") or row.get("filing_date") or "").strip()
    action_raw = str(row.get("trade_type") or row.get("transaction_type") or "").strip().lower()
    if not member or not tx_date or not disclosed or not action_raw:
        raise ValueError("required member, transaction date, filing date or action missing")
    tx = date.fromisoformat(tx_date)
    filing = date.fromisoformat(disclosed)
    if filing < tx:
        raise ValueError("filing date predates transaction date")
    if tx > date.today() or filing > date.today():
        raise ValueError("future-dated transaction or filing")
    actions = {"buy": "buy", "purchase": "buy", "purchased": "buy",
               "sell": "sell", "sale": "sell", "sold": "sell",
               "exchange": "exchange", "other": "other"}
    action = actions.get(action_raw)
    if not action:
        raise ValueError("unsupported trade type: " + action_raw)
    ticker = str(row.get("ticker") or "").strip().upper() or None
    if ticker and not re.fullmatch(r"[A-Z0-9.\-]{1,16}", ticker):
        ticker = None
    url = str(row.get("link") or row.get("filing_url") or "").strip() or None
    if url and not url.startswith(("https://", "http://")):
        url = None
    amount = str(row.get("amount") or row.get("amount_range") or "").strip() or None
    asset = str(row.get("asset") or row.get("asset_description") or "").strip() or None
    chamber = str(row.get("chamber") or "").strip() or None
    provider_id = row.get("id") or row.get("trade_id")
    if provider_id is not None:
        unique = {"provider": PROVIDER, "source_id": str(provider_id)}
    else:
        unique = {"provider": PROVIDER, "member": member.casefold(),
                  "ticker": ticker, "transaction_date": tx.isoformat(),
                  "filing_date": filing.isoformat(), "action": action,
                  "asset": asset, "amount": amount, "filing_url": url}
    event_id = hashlib.sha256(json.dumps(unique, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"event_id": event_id, "provider": PROVIDER, "member_name": member,
            "chamber": chamber, "ticker": ticker, "trade_type": action,
            "asset_description": asset, "amount_range": amount,
            "transaction_date": tx.isoformat(), "filing_date": filing.isoformat(),
            "filing_url": url, "raw_record": row}


def session_with_retries() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1.0,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"], respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def fetch_trades(session: requests.Session, *, days: int, limit: int,
                 base_url: str = DEFAULT_API) -> list[dict[str, Any]]:
    if not base_url.startswith("https://"):
        raise SourceError("API URL must use HTTPS")
    response = session.get(f"{base_url.rstrip('/')}/trades/recent",
                           params={"limit": limit, "days": days},
                           timeout=(10, 30), headers={"Accept": "application/json"})
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as exc:
        raise SourceError("API returned invalid JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("trades"), list):
        raise SourceError("API response must contain a trades array")
    if body.get("data_current") is False or body.get("cache_loading") is True:
        raise SourceError("API feed is not ready; refusing to sync")
    if not all(isinstance(t, dict) for t in body["trades"]):
        raise SourceError("API trades array contains malformed items")
    return body["trades"]


def ingest(trades: list[dict[str, Any]], db: Any, *, member_filter: str = "") -> dict[str, int]:
    accepted = {}
    invalid = 0
    for raw in trades:
        try:
            trade = normalize_trade(raw)
        except (ValueError, TypeError) as exc:
            invalid += 1
            LOG.warning("Disclosure rejected: %s", exc)
            continue
        if member_filter and member_filter.casefold() not in trade["member_name"].casefold():
            continue
        accepted[trade["event_id"]] = trade
    if trades and invalid == len(trades):
        raise SourceError("Every disclosure was invalid; no writes performed")
    records = list(accepted.values())
    for start in range(0, len(records), 100):
        db.table("politician_disclosures").upsert(
            records[start:start + 100],
            on_conflict="event_id", ignore_duplicates=True
        ).execute()
    return {"fetched": len(trades), "accepted": len(records), "invalid": invalid}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--member", default=os.getenv("POLITICIAN_MEMBER_FILTER", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not (1 <= args.days <= 365 and 1 <= args.limit <= 500):
        parser.error("days must be 1..365 and limit must be 1..500")
    try:
        trades = fetch_trades(session_with_retries(), days=args.days, limit=args.limit)
        if args.dry_run:
            rows = [normalize_trade(r) for r in trades]
            print(json.dumps({"fetched": len(trades), "validated": len(rows),
                              "sample": rows[:2]}, indent=2))
            return 0
        from supabase import create_client
        db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        run = db.table("politician_sync_runs").insert(
            {"provider": PROVIDER, "status": "running"}).execute()
        run_id = run.data[0]["run_id"]
        try:
            result = ingest(trades, db, member_filter=args.member)
            db.table("politician_sync_runs").update({
                "status": "success", "fetched": result["fetched"],
                "accepted": result["accepted"],
                "finished_at": datetime.now(timezone.utc).isoformat()
            }).eq("run_id", run_id).execute()
            print(json.dumps(result, sort_keys=True))
            return 0
        except Exception as exc:
            db.table("politician_sync_runs").update({
                "status": "failed", "error_message": str(exc)[:1000],
                "finished_at": datetime.now(timezone.utc).isoformat()
            }).eq("run_id", run_id).execute()
            raise
    except Exception as exc:
        LOG.exception("Congressional ingestion failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
