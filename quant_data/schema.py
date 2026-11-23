"""Versioned, provider-neutral market-data contracts. No I/O at import time."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

SCHEMA_VERSION = "1.0"
OHLCV = ("open", "high", "low", "close", "volume")
INTERVALS = {"1Min": 1, "1Hour": 60, "1Day": None}
ADJUSTMENTS = {"raw", "split", "dividend", "all", "unknown"}


def utc_stamp(value: datetime, name: str):
    """Reject unknown timezone instead of silently assuming UTC."""
    import pandas as pd
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return stamp.tz_convert("UTC")


@dataclass(frozen=True)
class BarSpec:
    symbol: str
    timeframe: str
    source: str
    feed: str
    adjustment: str
    exchange: str = "XNYS"

    def __post_init__(self):
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]*", self.symbol):
            raise ValueError("symbol must be uppercase and filename-safe")
        if self.timeframe not in INTERVALS:
            raise ValueError(f"unsupported timeframe: {self.timeframe}")
        if self.adjustment not in ADJUSTMENTS:
            raise ValueError("adjustment must be raw/split/dividend/all/unknown")
        if not self.source or not self.feed or not self.exchange:
            raise ValueError("source, feed and exchange are required")


@dataclass(frozen=True)
class QualityReport:
    rows: int
    duplicates: int
    invalid_ohlcv: int
    missing_bars: int
    off_session: int
    outside_window: int
    calendar_verified: bool

    @property
    def passed(self) -> bool:
        return (self.rows > 0 and self.calendar_verified and not any((
            self.duplicates, self.invalid_ohlcv, self.missing_bars,
            self.off_session, self.outside_window,
        )))


class DataQualityError(ValueError):
    """A snapshot failed strict validation; no silent repair is permitted."""


class StaleDatasetError(ValueError):
    """Provenance freshness requirement was not met."""
