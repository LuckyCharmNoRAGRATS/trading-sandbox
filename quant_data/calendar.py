"""Explicit authoritative session boundaries; never infer NYSE holidays from weekdays."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from .schema import INTERVALS, utc_stamp

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Session:
    opens_at: datetime
    closes_at: datetime

    def __post_init__(self):
        start = utc_stamp(self.opens_at, "opens_at")
        end = utc_stamp(self.closes_at, "closes_at")
        if start >= end or start.tz_convert(NY).date() != end.tz_convert(NY).date():
            raise ValueError("session must open before closing on the same NY date")


@dataclass(frozen=True)
class SessionCalendar:
    """Caller supplies actual exchange sessions, including holidays/early closes."""
    sessions: tuple[Session, ...]
    exchange: str = "XNYS"

    def __post_init__(self):
        dates = [utc_stamp(s.opens_at, "open").tz_convert(NY).date() for s in self.sessions]
        if dates != sorted(set(dates)):
            raise ValueError("sessions must be sorted with unique local dates")

    def expected(self, timeframe: str, start: datetime, end: datetime):
        if timeframe not in INTERVALS:
            raise ValueError("unsupported timeframe")
        left = utc_stamp(start, "start")
        right = utc_stamp(end, "end")
        if left >= right:
            raise ValueError("start must precede end")
        if timeframe == "1Day":
            return {
                utc_stamp(s.opens_at, "open").tz_convert(NY).date()
                for s in self.sessions
                if left <= utc_stamp(s.opens_at, "open") < right
            }
        minutes = INTERVALS[timeframe]
        stamps = set()
        for session in self.sessions:
            op = utc_stamp(session.opens_at, "open")
            cl = utc_stamp(session.closes_at, "close")
            for stamp in pd.date_range(op, cl, freq=f"{minutes}min", inclusive="left"):
                if left <= stamp < right:
                    stamps.add(stamp)
        return stamps

    def identify(self, index: pd.DatetimeIndex, timeframe: str):
        if timeframe == "1Day":
            return set(index.tz_convert(NY).date)
        return set(index)
