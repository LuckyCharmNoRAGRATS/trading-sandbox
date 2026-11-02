"""E2.0 proposed phase: point-in-time *synthetic-only* event study and E1 hold gate.

No network, database, broker, scheduled jobs, credential reads, live execution,
or original-filing verification. This does not consume historical Pelosi rows
as verified events. Synthetic examples are research mechanics, never signals.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from pathlib import Path
import re
from typing import Any

from congress_release_gate import verify_research_package, ReleaseGateError

E2_VERSION = "E2.0.0-offline-synthetic"
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,15}\Z")


class E2GateError(ValueError):
    """Incomplete research provenance, unsafe input, or potential hindsight bias."""


def _utc(value: Any, *, label: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise E2GateError(f"Invalid {label}") from exc
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise E2GateError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _row_object(row: Any, *, kind: str) -> dict:
    if not isinstance(row, dict) or row.get("dataset_kind") != "synthetic_fixture_only":
        raise E2GateError(f"{kind} must be an explicitly synthetic fixture")
    return row


def assess_real_source_hold(e123_dir: str | Path) -> dict:
    """Re-run the E1.24 SHA-linked hold gate; never enable actual event studies.

    The existing verifier accepts only the current zero-approved E1.23 package.
    Any future promoted source schema must receive a separate, explicitly
    reviewed E2 implementation rather than inheriting this synthetic pathway.
    """
    try:
        gate = verify_research_package(e123_dir)
    except (ReleaseGateError, ValueError, OSError) as exc:
        raise E2GateError("E1 evidence package failed strict release verification") from exc
    summary = gate["summary"]
    count = summary["total_source_rows"]
    if (summary.get("approved_for_ingestion_rows") != 0
            or summary.get("approved_for_trading_rows") != 0
            or summary.get("decision") != "NO_GO_OFFLINE_HOLD_ONLY"
            or len(gate["hold_queue"]) != count
            or any(item.get("eligible_for_execution") is not False
                   or item.get("eligible_for_ingestion") is not False
                   or item.get("first_seen_at") is not None
                   for item in gate["hold_queue"])):
        raise E2GateError("E1 source claims unexpected approval or reconstructed observation history")
    return {
        "version": E2_VERSION,
        "source_snapshot_sha256": summary["source_snapshot_sha256"],
        "real_disclosure_records_observed": count,
        "real_event_study_events_eligible": 0,
        "real_signal_count": 0,
        "unverified_source_records_held": count,
        "evidence_review_tasks_pending": summary["human_review_tasks_pending"],
        "unresolved_duplicate_groups": summary["pending_duplicate_groups"],
        "source_document_requests": summary["requested_filing_documents"],
        "missing_link_review_tasks": summary["missing_link_tasks"],
        "ready_for_real_event_study": False,
        "real_analysis_status": "NO_GO_PENDING_AUTHENTICATED_FILINGS_AND_TRUE_FIRST_OBSERVATIONS",
        "external_http_requests": 0,
        "supabase_writes": 0,
        "broker_orders": 0,
        "ingestion_approved": False,
        "trading_approved": False,
    }


def synthetic_close_to_close_study(
    events: list[dict], bars: list[dict], *, asof: datetime,
    forward_bars: int = 1,
) -> dict:
    """Compute *illustrative* post-observation price changes from synthetic data.

    The first reference is a synthetic bar close STRICTLY AFTER the simulated
    first-public-observation timestamp; this is NOT an assumed executable fill.
    Both the initial and future bars must be available as of the research cutoff.
    No real source is admitted, even if it has a user-set ``verified`` flag.
    """
    cutoff = _utc(asof, label="asof")
    if type(forward_bars) is not int or not 1 <= forward_bars <= 20:
        raise E2GateError("forward_bars must be an integer from 1 to 20")
    if not isinstance(events, list) or not isinstance(bars, list) or not events or not bars:
        raise E2GateError("Nonempty synthetic events and synthetic price bars are required")
    if len(events) > 200 or len(bars) > 10000:
        raise E2GateError("Synthetic research input exceeds bounded size")

    seen_events: set[str] = set()
    clean_events = []
    for raw in events:
        event = _row_object(raw, kind="event")
        event_id, ticker = event.get("event_id"), event.get("ticker")
        if (not isinstance(event_id, str) or _ID.fullmatch(event_id) is None
                or event_id in seen_events):
            raise E2GateError("Missing or duplicate synthetic event ID")
        seen_events.add(event_id)
        if not isinstance(ticker, str) or _TICKER.fullmatch(ticker) is None:
            raise E2GateError("Invalid synthetic ticker")
        if event.get("asset_type") != "Stock" or event.get("action") not in ("Purchase", "Sale"):
            raise E2GateError("Synthetic study accepts named common stock actions only")
        if event.get("member") != "Synthetic Example" or event.get("owner") not in ("Self", "Spouse"):
            raise E2GateError("Synthetic member/ownership provenance is required")
        first_seen = _utc(event.get("first_seen_at"), label="synthetic first_seen_at")
        if first_seen > cutoff:
            raise E2GateError("Synthetic event first observation lies after asof")
        if event.get("official_filing_verified") is True:
            raise E2GateError("Synthetic fixture must not claim a real verified filing")
        if event.get("semantic_duplicate_resolved") is not True:
            raise E2GateError("Unresolved synthetic duplicate identity")
        clean_events.append((event_id, ticker, event["action"], event["owner"], first_seen))

    prices: dict[str, list[tuple[datetime, datetime, float]]] = defaultdict(list)
    observed_bar_keys: set[tuple[str, datetime]] = set()
    for raw in bars:
        bar = _row_object(raw, kind="market bar")
        ticker = bar.get("ticker")
        if not isinstance(ticker, str) or _TICKER.fullmatch(ticker) is None:
            raise E2GateError("Invalid synthetic price ticker")
        bar_time = _utc(bar.get("bar_time"), label="bar_time")
        available = _utc(bar.get("available_at"), label="available_at")
        if available < bar_time or available > cutoff or bar_time > cutoff:
            raise E2GateError("Price bar availability violates research asof")
        value = bar.get("adjusted_close")
        if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
            raise E2GateError("Price bar requires positive finite adjusted close")
        key = (ticker, bar_time)
        if key in observed_bar_keys:
            raise E2GateError("Duplicate synthetic ticker/bar timestamp")
        observed_bar_keys.add(key)
        prices[ticker].append((bar_time, available, float(value)))
    for xs in prices.values():
        xs.sort(key=lambda x: x[0])

    rows = []
    for event_id, ticker, action, owner, first_seen in sorted(clean_events):
        series = [bar for bar in prices.get(ticker, [])
                  if bar[0] > first_seen and bar[1] > first_seen]
        if len(series) < forward_bars + 1:
            raise E2GateError("Insufficient strictly post-observation synthetic price bars")
        start, end = series[0], series[forward_bars]
        if start[1] > end[0]:
            raise E2GateError("Reference price was not available before the forward bar")
        rows.append({
            "synthetic_event_id": event_id,
            "ticker": ticker,
            "owner_type": owner,
            "reported_action": action,
            "first_seen_at": first_seen.isoformat(),
            "reference_bar_time": start[0].isoformat(),
            "forward_bar_time": end[0].isoformat(),
            "illustrative_forward_price_change_fraction": (end[2] / start[2]) - 1,
            "fill_price_inferred": False,
            "trade_signal": False,
        })
    return {
        "version": E2_VERSION,
        "status": "SYNTHETIC_EDUCATIONAL_EVENT_STUDY_ONLY",
        "data_origin": "synthetic_fixture_only",
        "synthetic_events_evaluated": len(rows),
        "real_events_evaluated": 0,
        "asof_utc": cutoff.isoformat(),
        "forward_bar_count": forward_bars,
        "rows": rows,
        "trading_approved": False,
        "ingestion_approved": False,
        "supabase_writes": 0,
        "broker_orders": 0,
        "external_http_requests": 0,
        "research_limitations": [
            "Hypothetical close-to-close mark, not an executable strategy or P&L",
            "No survivorship or corporate-action audit, transaction costs, slippage, option pricing, or execution model",
            "Synthetic timestamps do not establish real congressional filing observability",
        ],
    }
