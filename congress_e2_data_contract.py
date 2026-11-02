"""E2.1: immutable synthetic disclosure and revision-aware market-data contract.

No real congressional records can enter this pathway. No web/network, broker,
Supabase, cron, credentials or persistent mutations. This is a deterministic
point-in-time research fixture interface; synthetic labels are not proof that
externally supplied content is genuine or licensed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from congress_e2_research import E2GateError, synthetic_close_to_close_study

SCHEMA_VERSION = "E2.1.0-synthetic"
SYNTHETIC = "synthetic_fixture_only"
ADJUSTMENT_POLICY = "synthetic_no_corporate_actions"
MAX_BYTES = 2_000_000
MAX_EVENTS = 200
MAX_BAR_REVISIONS = 10_000
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,15}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
TOP_FIELDS = frozenset({"schema_version", "dataset_id", "dataset_kind",
                        "price_adjustment_policy", "events", "bar_revisions"})
EVENT_FIELDS = frozenset({"dataset_kind", "event_id", "member", "ticker",
                          "owner", "asset_type", "action", "first_seen_at",
                          "semantic_duplicate_resolved", "official_filing_verified",
                          "transaction_date", "filing_date", "synthetic_source_id"})
BAR_FIELDS = frozenset({"dataset_kind", "ticker", "bar_time", "available_at",
                        "adjusted_close", "revision"})


class E21ContractError(ValueError):
    """Unsafe, ambiguous or incomplete synthetic research fixture."""


def _utc(value: Any, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise E21ContractError(f"Invalid {name}") from exc
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise E21ContractError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _date(value: Any, name: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise E21ContractError(f"{name} requires YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise E21ContractError(f"Invalid {name}") from exc


def _canonical(item: Any) -> bytes:
    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise E21ContractError("Fixture cannot be encoded as finite UTF-8 JSON") from exc


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fields(item: Any, expected: frozenset[str], label: str) -> dict:
    if not isinstance(item, dict) or frozenset(item) != expected:
        raise E21ContractError(f"{label} requires exact, versioned schema keys")
    return item


def _label(item: dict, label: str) -> None:
    if item["dataset_kind"] != SYNTHETIC:
        raise E21ContractError(f"{label}: synthetic-only provenance required")


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise E21ContractError(f"Invalid {label}")
    return value


def _ticker(value: Any) -> str:
    if not isinstance(value, str) or _TICKER.fullmatch(value) is None:
        raise E21ContractError("Invalid synthetic ticker")
    return value


def _parse_event(value: Any) -> dict:
    raw = _fields(value, EVENT_FIELDS, "synthetic event")
    _label(raw, "event")
    _id(raw["event_id"], "event_id")
    _id(raw["synthetic_source_id"], "synthetic_source_id")
    _ticker(raw["ticker"])
    if (raw["member"] != "Synthetic Example" or raw["owner"] not in ("Self", "Spouse")
            or raw["asset_type"] != "Stock" or raw["action"] not in ("Purchase", "Sale")):
        raise E21ContractError("Only synthetic common-stock actions and ownership accepted")
    if raw["semantic_duplicate_resolved"] is not True:
        raise E21ContractError("Synthetic duplicate resolution is required")
    if raw["official_filing_verified"] is not False:
        raise E21ContractError("Synthetic fixture must not assert official filing authentication")
    transaction_date = _date(raw["transaction_date"], "transaction_date")
    filing_date = _date(raw["filing_date"], "filing_date")
    first_seen = _utc(raw["first_seen_at"], "first_seen_at")
    if not transaction_date <= filing_date <= first_seen.date():
        raise E21ContractError("Transaction/filing/first-seen chronology invalid")
    normalized = dict(raw)
    normalized["first_seen_at"] = first_seen.isoformat()
    return normalized


def _parse_bar(value: Any) -> dict:
    raw = _fields(value, BAR_FIELDS, "market bar revision")
    _label(raw, "bar")
    _ticker(raw["ticker"])
    at = _utc(raw["bar_time"], "bar_time")
    available = _utc(raw["available_at"], "available_at")
    if available < at:
        raise E21ContractError("Price revision cannot be observed before its bar time")
    price = raw["adjusted_close"]
    if type(price) not in (float, int) or not math.isfinite(price) or price <= 0:
        raise E21ContractError("Positive finite synthetic price required")
    if type(raw["revision"]) is not int or not 1 <= raw["revision"] <= 100:
        raise E21ContractError("Revision must be an integer from 1 to 100")
    normalized = dict(raw)
    normalized.update({"bar_time": at.isoformat(),
                       "available_at": available.isoformat(),
                       "adjusted_close": float(price)})
    return normalized


@dataclass(frozen=True, slots=True)
class SyntheticDataset:
    """Stores only immutable canonical JSON bytes; never exposes mutable backing rows."""
    dataset_id: str
    canonical_sha256: str
    source_file_sha256: str | None
    _canonical_payload: bytes

    def records(self) -> dict:
        # A fresh copy is returned; caller mutation cannot change the dataset.
        return json.loads(self._canonical_payload)


def validate_synthetic_dataset(payload: Any, *, source_file_sha256: str | None = None) -> SyntheticDataset:
    root = _fields(payload, TOP_FIELDS, "E2.1 dataset")
    if root["schema_version"] != SCHEMA_VERSION or root["dataset_kind"] != SYNTHETIC:
        raise E21ContractError("Unrecognized version or real-source dataset rejected")
    if root["price_adjustment_policy"] != ADJUSTMENT_POLICY:
        raise E21ContractError("Corporate actions/adjustments must remain explicitly unaudited")
    dataset_id = _id(root["dataset_id"], "dataset_id")
    if (not isinstance(root["events"], list) or not 1 <= len(root["events"]) <= MAX_EVENTS
            or not isinstance(root["bar_revisions"], list)
            or not 1 <= len(root["bar_revisions"]) <= MAX_BAR_REVISIONS):
        raise E21ContractError("Nonempty bounded events and bar revisions required")
    if source_file_sha256 is not None and (not isinstance(source_file_sha256, str)
                                            or _SHA.fullmatch(source_file_sha256) is None):
        raise E21ContractError("Invalid source file digest")

    events = [_parse_event(x) for x in root["events"]]
    bars = [_parse_bar(x) for x in root["bar_revisions"]]
    ids = set()
    semantics = set()
    for event in events:
        identity = event["event_id"]
        semantic = (event["ticker"], event["owner"], event["action"], event["first_seen_at"],
                    event["synthetic_source_id"])
        if identity in ids or semantic in semantics:
            raise E21ContractError("Duplicate event identity or semantic identity")
        ids.add(identity)
        semantics.add(semantic)
    by_bar: dict[tuple[str, str], list[dict]] = {}
    for bar in bars:
        by_bar.setdefault((bar["ticker"], bar["bar_time"]), []).append(bar)
    for versions in by_bar.values():
        ordered = sorted(versions, key=lambda x: x["revision"])
        if [item["revision"] for item in ordered] != list(range(1, len(ordered) + 1)):
            raise E21ContractError("Missing, duplicated or nonmonotonic revision numbers")
        if any(ordered[i]["available_at"] >= ordered[i + 1]["available_at"]
               for i in range(len(ordered) - 1)):
            raise E21ContractError("Price revision availability must strictly increase")
    if not {e["ticker"] for e in events} <= {b["ticker"] for b in bars}:
        raise E21ContractError("Missing ticker price series for a synthetic event")
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_kind": SYNTHETIC,
        "price_adjustment_policy": ADJUSTMENT_POLICY,
        "events": sorted(events, key=lambda x: x["event_id"]),
        "bar_revisions": sorted(bars, key=lambda x: (x["ticker"], x["bar_time"], x["revision"])),
    }
    canonical = _canonical(normalized)
    if len(canonical) > MAX_BYTES:
        raise E21ContractError("Canonical dataset exceeds bounded size")
    return SyntheticDataset(dataset_id, _digest(canonical), source_file_sha256, canonical)


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise E21ContractError("Duplicate JSON object key in local snapshot")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise E21ContractError(f"Nonfinite JSON number {value} is forbidden")


def load_synthetic_snapshot(path: str | Path, *, expected_file_sha256: str | None = None) -> SyntheticDataset:
    """Bounded regular local file read. No URL, symlink, remote path or JSON side effects."""
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise E21ContractError("Only a regular local JSON file is accepted")
    size = file.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise E21ContractError("Local snapshot is empty or exceeds the byte limit")
    data = file.read_bytes()
    if len(data) != size:
        raise E21ContractError("Local snapshot size changed during read")
    digest = _digest(data)
    if expected_file_sha256 is not None and digest != expected_file_sha256:
        raise E21ContractError("Source file SHA-256 mismatch")
    try:
        value = json.loads(data.decode("utf-8", errors="strict"),
                           object_pairs_hook=_no_duplicate_keys,
                           parse_constant=_invalid_constant)
    except E21ContractError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise E21ContractError("Local snapshot is not strict UTF-8 JSON") from exc
    return validate_synthetic_dataset(value, source_file_sha256=digest)


def snapshot_asof(dataset: SyntheticDataset, *, asof: datetime) -> dict:
    """Return only data known at cutoff, picking the latest *visible* revision.

    Future revisions are not returned and their existence is not revealed in
    the as-of result. This is NOT a corporate-actions or survivorship audit.
    """
    if not isinstance(dataset, SyntheticDataset):
        raise E21ContractError("An E2.1 validated synthetic dataset is required")
    cutoff = _utc(asof, "asof")
    payload = dataset.records()
    # Revalidate even manually constructed dataclass instances: no forged
    # synthetic wrapper can bypass the full versioned data contract.
    checked = validate_synthetic_dataset(payload, source_file_sha256=dataset.source_file_sha256)
    if (checked.canonical_sha256 != dataset.canonical_sha256
            or checked._canonical_payload != dataset._canonical_payload
            or checked.dataset_id != dataset.dataset_id):
        raise E21ContractError("Dataset immutable provenance digest mismatch")
    events = payload["events"]
    if any(_utc(event["first_seen_at"], "first_seen_at") > cutoff for event in events):
        raise E21ContractError("Future first-seen event cannot be used in an as-of study")
    selected: dict[tuple[str, str], dict] = {}
    for bar in payload["bar_revisions"]:
        observed = _utc(bar["available_at"], "available_at")
        at = _utc(bar["bar_time"], "bar_time")
        if observed > cutoff or at > cutoff:
            continue
        key = (bar["ticker"], bar["bar_time"])
        prior = selected.get(key)
        if prior is None or bar["revision"] > prior["revision"]:
            selected[key] = bar
    view = []
    for bar in sorted(selected.values(), key=lambda x: (x["ticker"], x["bar_time"])):
        view.append({k: bar[k] for k in ("dataset_kind", "ticker", "bar_time",
                                          "available_at", "adjusted_close")})
    if not view:
        raise E21ContractError("No synthetic market bars visible at cutoff")
    visible = {"dataset_kind": SYNTHETIC, "asof_utc": cutoff.isoformat(),
               "events": events, "bars": view,
               "real_event_count": 0,
               "trade_signal": False, "approved_for_ingestion": False,
               "approved_for_trading": False}
    visible["visible_snapshot_sha256"] = _digest(_canonical(visible))
    return visible


def study_synthetic_snapshot(dataset: SyntheticDataset, *, asof: datetime,
                             forward_bars: int = 1) -> dict:
    """Adapt strict E2.1 as-of view to E2.0's educational event study."""
    view = snapshot_asof(dataset, asof=asof)
    try:
        report = synthetic_close_to_close_study(view["events"], view["bars"],
                                                asof=asof, forward_bars=forward_bars)
    except E2GateError as exc:
        raise E21ContractError("E2.0 event study rejected as-of input: " + str(exc)) from exc
    return {**report, "e21_schema_version": SCHEMA_VERSION,
            "e21_dataset_id": dataset.dataset_id,
            "e21_visible_snapshot_sha256": view["visible_snapshot_sha256"],
            "e21_source_canonical_sha256": dataset.canonical_sha256,
            "source_file_sha256": dataset.source_file_sha256,
            "source_authentication_completed": False,
            "historical_price_license_audited": False,
            "corporate_actions_audited": False,
            "trade_signal": False}
