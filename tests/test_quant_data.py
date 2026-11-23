"""Synthetic, broker-free tests for contract v1."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import importlib.util
import pandas as pd
import pytest
from quant_data import (
    BarSpec, Session, SessionCalendar, DataQualityError, StaleDatasetError,
    build_dataset, normalize_bars, read_dataset, write_dataset,
)

NY = ZoneInfo("America/New_York")
SPEC = BarSpec("SPY", "1Day", "alpaca", "iex", "all")


def stamp(day, clock):
    return pd.Timestamp(f"{day} {clock}", tz=NY)


def fixture():
    sessions = SessionCalendar(tuple(Session(stamp(day, "09:30"), stamp(day, "16:00"))
                                     for day in ("2026-10-30", "2026-11-02", "2026-11-03")))
    idx = pd.DatetimeIndex([stamp(day, "00:00") for day in ("2026-10-30", "2026-11-02", "2026-11-03")])
    bars = pd.DataFrame({"open": [100, 101, 102], "high": [102, 103, 104],
                         "low": [99, 100, 101], "close": [101, 102, 103],
                         "volume": [1000, 1200, 1500]}, index=idx)
    start, end = stamp("2026-10-30", "00:00"), stamp("2026-11-04", "00:00")
    return bars, sessions, start, end


def dataset(bars=None, sessions=None, start=None, end=None, strict=True):
    base, cal, first, last = fixture()
    return build_dataset(base if bars is None else bars, spec=SPEC,
                         calendar=cal if sessions is None else sessions,
                         start=first if start is None else start,
                         end=last if end is None else end,
                         retrieved_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
                         strict=strict)


def test_day_calendar_handles_dst_weekend_and_no_phantom_bars():
    result = dataset()
    assert result.quality.passed
    assert list(result.bars.index.tz_convert(NY).hour) == [0, 0, 0]
    assert result.bars.index[0].hour == 4  # EDT
    assert result.bars.index[1].hour == 5  # EST
    assert result.manifest["spec"]["adjustment"] == "all"
    assert result.manifest["schema_version"] == "1.0"


def test_naive_timestamps_rejected_not_guessed():
    bars, *_ = fixture()
    bars.index = bars.index.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize_bars(bars, SPEC)


def test_duplicates_detected_before_any_deduplication():
    bars, *_ = fixture()
    dirty = pd.concat([bars, bars.iloc[[0]]])
    assert dataset(bars=dirty, strict=False).quality.duplicates == 1
    with pytest.raises(DataQualityError):
        dataset(bars=dirty)


def test_missing_trading_session_reported_without_forward_fill():
    bars, *_ = fixture()
    reported = dataset(bars=bars.iloc[[0, 2]], strict=False)
    assert reported.quality.missing_bars == 1
    assert len(reported.bars) == 2
    with pytest.raises(DataQualityError):
        dataset(bars=bars.iloc[[0, 2]])


def test_invalid_price_and_negative_volume_detected():
    bars, *_ = fixture()
    bars.iloc[1, bars.columns.get_loc("high")] = 99
    bars.iloc[2, bars.columns.get_loc("volume")] = -1
    assert dataset(bars=bars, strict=False).quality.invalid_ohlcv == 2


def test_extra_unexpected_session_flagged():
    bars, *_ = fixture()
    extra = bars.iloc[[0]].copy()
    extra.index = pd.DatetimeIndex([stamp("2026-10-31", "00:00")])
    report = dataset(bars=pd.concat([bars, extra]), strict=False).quality
    assert report.off_session == 1


def test_missing_calendar_never_claims_verified_data():
    bars, _, start, end = fixture()
    unverified = build_dataset(bars, spec=SPEC, start=start, end=end, calendar=None, strict=False)
    assert not unverified.quality.calendar_verified
    assert not unverified.quality.passed
    with pytest.raises(DataQualityError):
        build_dataset(bars, spec=SPEC, start=start, end=end, calendar=None)


def test_multiindex_alpaca_adapter_normalizes_symbol():
    bars, *_ = fixture()
    frame = bars.copy()
    frame["symbol"] = "SPY"
    frame = frame.set_index("symbol", append=True).reorder_levels([1, 0])
    frame.index.names = ["symbol", "timestamp"]
    assert normalize_bars(frame, SPEC).equals(normalize_bars(bars, SPEC))


def test_minute_grid_reports_absent_minute_and_respects_early_close():
    session = Session(stamp("2026-11-02", "09:30"), stamp("2026-11-02", "09:33"))
    cal = SessionCalendar((session,))
    ts = pd.DatetimeIndex([stamp("2026-11-02", "09:30"), stamp("2026-11-02", "09:32")])
    frame = pd.DataFrame({"open": [1, 1], "high": [2, 2], "low": [1, 1],
                          "close": [1.5, 1.5], "volume": [2, 2]}, index=ts)
    spec = BarSpec("SPY", "1Min", "alpaca", "iex", "raw")
    result = build_dataset(frame, spec=spec, calendar=cal,
                           start=stamp("2026-11-02", "09:30"), end=stamp("2026-11-02", "09:33"),
                           strict=False)
    assert result.quality.missing_bars == 1
    assert result.quality.off_session == 0


def test_outside_requested_window_fails_closed():
    bars, _, _, end = fixture()
    result = dataset(bars=bars, start=stamp("2026-11-02", "00:00"), end=end, strict=False)
    assert result.quality.outside_window == 1


@pytest.mark.skipif(importlib.util.find_spec("pyarrow") is None, reason="pyarrow not installed locally")
def test_parquet_roundtrip_and_copy_isolation(tmp_path):
    value = dataset()
    path = write_dataset(value, tmp_path)
    restored = read_dataset(tmp_path, value.manifest["dataset_id"],
                            max_age=timedelta(days=2), now=datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert path.exists()
    assert restored.quality.passed
    assert restored.bars.equals(value.bars)
    copied = restored.to_legacy_frame()
    copied.iloc[0, 0] = 0
    assert restored.bars.iloc[0, 0] == 100
    with pytest.raises(FileExistsError):
        write_dataset(value, tmp_path)
    with pytest.raises(StaleDatasetError):
        read_dataset(tmp_path, value.manifest["dataset_id"],
                     max_age=timedelta(hours=1), now=datetime(2026, 11, 6, tzinfo=timezone.utc))


@pytest.mark.skipif(importlib.util.find_spec("pyarrow") is None, reason="pyarrow not installed locally")
def test_parquet_content_tamper_fails_closed(tmp_path):
    value = dataset()
    path = write_dataset(value, tmp_path)
    frame = pd.read_parquet(path)
    frame.loc[frame.index[0], "close"] = 999
    frame.to_parquet(path)
    with pytest.raises(DataQualityError, match="integrity"):
        read_dataset(tmp_path, value.manifest["dataset_id"])


@pytest.mark.skipif(importlib.util.find_spec("pyarrow") is None, reason="pyarrow not installed locally")
def test_manifest_tamper_fails_closed(tmp_path):
    import json
    value = dataset()
    write_dataset(value, tmp_path)
    sidecar = tmp_path / f"{value.manifest['dataset_id']}.manifest.json"
    manifest = json.loads(sidecar.read_text())
    manifest["quality"]["missing_bars"] = 2
    sidecar.write_text(json.dumps(manifest))
    with pytest.raises(DataQualityError, match="manifest integrity"):
        read_dataset(tmp_path, value.manifest["dataset_id"])
