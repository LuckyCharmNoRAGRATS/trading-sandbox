"""Offline storage-protocol tests. Fake Parquet backend; not a real Parquet test."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pandas as pd
import pytest

from quant_data import (
    BarSpec, DataQualityError, Session, SessionCalendar, StaleDatasetError,
    build_dataset, read_dataset, write_dataset,
)


def make_dataset():
    ny = "America/New_York"
    day = "2026-11-02"
    frame = pd.DataFrame({"open": [100.0], "high": [102.0], "low": [99.0],
                          "close": [101.0], "volume": [1000.0]},
                         index=pd.DatetimeIndex([pd.Timestamp(f"{day} 00:00", tz=ny)]))
    session = Session(pd.Timestamp(f"{day} 09:30", tz=ny), pd.Timestamp(f"{day} 16:00", tz=ny))
    return build_dataset(
        frame, spec=BarSpec("SPY", "1Day", "alpaca", "iex", "all"),
        calendar=SessionCalendar((session,)),
        start=pd.Timestamp(f"{day} 00:00", tz=ny),
        end=pd.Timestamp("2026-11-03 00:00", tz=ny),
        retrieved_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
    )


@pytest.fixture
def fake_parquet(monkeypatch):
    """Protocol stub: local synthetic frames only; never used by production code."""
    monkeypatch.setattr(pd.DataFrame, "to_parquet",
                        lambda self, path, index=True: self.to_pickle(path))
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.read_pickle(path))


def test_snapshot_roundtrip_copy_and_freshness_with_backend_stub(tmp_path, fake_parquet):
    snapshot = make_dataset()
    out = write_dataset(snapshot, tmp_path)
    assert out.exists()
    read = read_dataset(tmp_path, snapshot.manifest["dataset_id"],
                        max_age=timedelta(days=3),
                        now=datetime(2026, 11, 4, tzinfo=timezone.utc))
    assert read.bars.equals(snapshot.bars)
    assert read.quality.passed
    legacy = read.to_legacy_frame()
    legacy.iloc[0, 0] = 0
    assert read.bars.iloc[0, 0] == 100
    with pytest.raises(FileExistsError):
        write_dataset(snapshot, tmp_path)
    with pytest.raises(StaleDatasetError):
        read_dataset(tmp_path, snapshot.manifest["dataset_id"],
                     max_age=timedelta(hours=1),
                     now=datetime(2026, 11, 4, tzinfo=timezone.utc))


def test_changed_snapshot_file_rejected_with_backend_stub(tmp_path, fake_parquet):
    snapshot = make_dataset()
    path = write_dataset(snapshot, tmp_path)
    modified = pd.read_pickle(path)
    modified.iloc[0, 3] = 999.0
    modified.to_pickle(path)
    with pytest.raises(DataQualityError, match="integrity"):
        read_dataset(tmp_path, snapshot.manifest["dataset_id"])


def test_changed_manifest_rejected_with_backend_stub(tmp_path, fake_parquet):
    snapshot = make_dataset()
    write_dataset(snapshot, tmp_path)
    sidecar = tmp_path / f"{snapshot.manifest['dataset_id']}.manifest.json"
    manifest = json.loads(sidecar.read_text())
    manifest["quality"]["missing_bars"] = 1
    sidecar.write_text(json.dumps(manifest))
    with pytest.raises(DataQualityError, match="manifest integrity"):
        read_dataset(tmp_path, snapshot.manifest["dataset_id"])
