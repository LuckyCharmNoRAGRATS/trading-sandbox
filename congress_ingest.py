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
from pathlib import Path

from congress_quality import (FeedIntegrityError, validate_tracefour_feed,
                              parse_timestamp, MAX_CACHE_AGE, MAX_CLOCK_SKEW)
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger("congress_ingest")
DEFAULT_API = "https://congressinfor-production.up.railway.app"
PROVIDER = "congressinvests"
TRACEFOUR_URL = "https://tracefour.com/v1/congress/nancy-pelosi"


class SourceError(RuntimeError):
    """Invalid or unavailable source response."""


def normalize_trade(row: dict[str, Any], *, provider: str = PROVIDER) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("trade must be an object")
    member = str(row.get("member") or row.get("politician") or row.get("memberName") or "").strip()
    tx_date = str(row.get("tx_date") or row.get("transaction_date") or row.get("transactionDate") or "").strip()
    disclosed = str(row.get("disclosed") or row.get("filing_date") or row.get("disclosureDate") or "").strip()
    action_raw = str(row.get("trade_type") or row.get("transaction_type") or row.get("type") or "").strip().lower()
    if not member or not tx_date or not disclosed or not action_raw:
        raise ValueError("required member, transaction date, filing date or action missing")
    tx = date.fromisoformat(tx_date)
    filing = date.fromisoformat(disclosed)
    if filing < tx:
        raise ValueError("filing date predates transaction date")
    if tx > datetime.now(timezone.utc).date() or filing > datetime.now(timezone.utc).date():
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
    url = str(row.get("link") or row.get("filing_url") or row.get("sourceLink") or "").strip() or None
    if url and not url.startswith(("https://", "http://")):
        url = None
    amount = str(row.get("amount") or row.get("amount_range") or row.get("amountLabel") or "").strip() or None
    asset = str(row.get("asset") or row.get("asset_description") or row.get("assetDescription") or "").strip() or None
    chamber = str(row.get("chamber") or "").strip() or None
    provider_id = row.get("id") or row.get("trade_id") or row.get("filingId")
    if provider == "tracefour":
        if member.casefold() != "nancy pelosi" or not row.get("filingId"):
            raise ValueError("Tracefour member identity or filing identifier missing")
        if not url or not url.startswith("https://"):
            raise ValueError("Tracefour original HTTPS filing link required")
    if provider_id is not None:
        unique = {"provider": provider, "source_id": str(provider_id)}
    else:
        unique = {"provider": provider, "member": member.casefold(),
                  "ticker": ticker, "transaction_date": tx.isoformat(),
                  "filing_date": filing.isoformat(), "action": action,
                  "asset": asset, "amount": amount, "filing_url": url}
    event_id = hashlib.sha256(json.dumps(unique, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"event_id": event_id, "provider": provider, "member_name": member,
            "chamber": chamber, "ticker": ticker, "trade_type": action,
            "asset_description": asset, "amount_range": amount,
            "transaction_date": tx.isoformat(), "filing_date": filing.isoformat(),
            "filing_url": url, "raw_record": row,
            "asset_type": str(row.get("assetType") or row.get("asset_type") or "").strip() or None,
            "owner_type": str(row.get("owner") or "").strip() or None,
            "upstream_ingested_at": row.get("ingestedAt") or None}


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
    if not body["trades"] or len(body["trades"]) >= limit:
        raise SourceError("CongressInvests empty or at page limit: completeness unverified")
    try:
        cache_time = parse_timestamp(body.get("last_updated"), field="last_updated")
    except FeedIntegrityError as exc:
        raise SourceError("CongressInvests source freshness metadata missing") from exc
    lag = datetime.now(timezone.utc) - cache_time
    if lag > MAX_CACHE_AGE or lag < -MAX_CLOCK_SKEW:
        raise SourceError("CongressInvests freshness not verified")
    return body["trades"]


def fetch_tracefour(session: requests.Session, *, url: str = TRACEFOUR_URL,
                    now: datetime | None = None) -> list[dict[str, Any]]:
    """Validate the documented data/member + meta.fetchedAt envelope.

    No requests occur unless the caller supplies an explicit session and calls
    this function. A response at the 500-record cap is quarantined.
    """
    if url != TRACEFOUR_URL:
        raise SourceError("Tracefour member URL must be the documented HTTPS endpoint")
    response = session.get(url, timeout=(10, 30), headers={"Accept": "application/json"})
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as exc:
        raise SourceError("Tracefour returned invalid JSON") from exc
    try:
        return validate_tracefour_feed(body, now=now)["trades"]
    except FeedIntegrityError as exc:
        raise SourceError(f"Tracefour feed not suitable for ingestion: {exc}") from exc


def ingest(trades: list[dict[str, Any]], db: Any, *, member_filter: str = "",
           provider: str = PROVIDER) -> dict[str, int]:
    """All-or-nothing row validation BEFORE any write is attempted.

    DB upsert chunking is not a cross-request transaction; if a later chunk
    fails, earlier chunks may have committed. Keep this as a NO-GO until a
    transactional ingestion RPC exists and has been tested independently.
    """
    if not isinstance(trades, list) or not trades:
        raise SourceError("Empty disclosure batch; no writes performed")
    accepted = {}
    invalid = 0
    for raw in trades:
        try:
            trade = normalize_trade(raw, provider=provider)
        except (ValueError, TypeError) as exc:
            invalid += 1
            LOG.warning("Disclosure rejected: %s", exc)
            continue
        if member_filter and member_filter.casefold() not in trade["member_name"].casefold():
            continue
        previous = accepted.get(trade["event_id"])
        if previous is not None and previous != trade:
            raise SourceError("Conflicting source rows share an event identifier")
        accepted[trade["event_id"]] = trade
    if invalid:
        raise SourceError(f"{invalid} invalid disclosure(s); refusing partial import")
    if not accepted:
        raise SourceError("No matching disclosure records; no writes performed")
    records = list(accepted.values())
    if len(records) > 100:
        raise SourceError("Batch exceeds single-transaction bound; no writes performed")
    # A single SQL upsert statement avoids the previous partial multi-chunk
    # commit hazard, but does not make sync-run logging transactional.
    db.table("politician_disclosures").upsert(
        records, on_conflict="event_id", ignore_duplicates=True
    ).execute()
    return {"fetched": len(trades), "accepted": len(records), "invalid": invalid}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--member", default=os.getenv("POLITICIAN_MEMBER_FILTER", ""))
    parser.add_argument("--dry-run", action="store_true",
                        help="network fetch preview only after explicit approval")
    parser.add_argument("--offline-fixture", type=Path,
                        help="validate a local synthetic envelope; never contact services")
    parser.add_argument("--allow-network-fetch", action="store_true",
                        help="future opt-in only, requires prior user approval")
    parser.add_argument("--approve-database-write", action="store_true",
                        help="future data-only sync, also needs CONGRESS_SYNC_APPROVED")
    parser.add_argument("--provider", choices=["tracefour", "congressinvests"], default="tracefour")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not (1 <= args.days <= 365 and 1 <= args.limit <= 500):
        parser.error("days must be 1..365 and limit must be 1..500")
    if args.offline_fixture is not None:
        if args.allow_network_fetch or args.approve_database_write or args.provider != "tracefour":
            parser.error("offline fixture cannot be combined with live operations")
        try:
            body = json.loads(args.offline_fixture.read_text(encoding="utf-8"))
            # Frozen fixture time tests schema, not current provider freshness.
            fixture_at = parse_timestamp(body["meta"]["fetchedAt"], field="fixture fetchedAt")
            checked = validate_tracefour_feed(body, now=fixture_at)
            records = [normalize_trade(item, provider="tracefour") for item in checked["trades"]]
            print(json.dumps({"mode": "synthetic_fixture_only", "validated": len(records),
                              "live_data_verified": False, "database_writes": False}, sort_keys=True))
            return 0
        except (OSError, ValueError, KeyError, TypeError, FeedIntegrityError) as exc:
            LOG.error("Offline fixture failed: %s", exc)
            return 1
    # Fail closed BEFORE constructing HTTP/Supabase clients. This is only a
    # software safeguard, not a substitute for explicit human approval.
    if not args.allow_network_fetch:
        parser.error("Network disabled: explicit --allow-network-fetch required")
    if not args.dry_run and not (args.approve_database_write and
                                 os.getenv("CONGRESS_SYNC_APPROVED") == "data-only"):
        parser.error("Database ingestion disabled pending explicit approval")
    try:
        session = session_with_retries()
        trades = (fetch_tracefour(session) if args.provider == "tracefour"
                  else fetch_trades(session, days=args.days, limit=args.limit))
        if args.dry_run:
            rows = [normalize_trade(r, provider=args.provider) for r in trades]
            print(json.dumps({"fetched": len(trades), "validated": len(rows),
                              "sample": rows[:2]}, indent=2))
            return 0
        from supabase import create_client
        db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        run = db.table("politician_sync_runs").insert(
            {"provider": args.provider, "status": "running"}).execute()
        run_id = run.data[0]["run_id"]
        try:
            result = ingest(trades, db, member_filter=args.member, provider=args.provider)
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
