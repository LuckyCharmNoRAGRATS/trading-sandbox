"""Offline immutable Parquet snapshots with integrity-checked JSON manifests."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import pandas as pd
from .calendar import SessionCalendar
from .quality import audit_bars, normalize_bars
from .schema import BarSpec, DataQualityError, QualityReport, SCHEMA_VERSION, StaleDatasetError, utc_stamp


def _frame_digest(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(float_format="%.17g", date_format="%Y-%m-%dT%H:%M:%S.%f%z")
    return sha256(payload.encode("utf-8")).hexdigest()


def _version_id(content_sha256: str, spec: dict, start: str, end: str) -> str:
    canonical = json.dumps({"schema": SCHEMA_VERSION, "content": content_sha256,
                            "spec": spec, "start": start, "end": end}, sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _manifest_digest(manifest: dict) -> str:
    fields = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Dataset:
    bars: pd.DataFrame
    manifest: dict
    quality: QualityReport

    def to_legacy_frame(self) -> pd.DataFrame:
        """Copy-only adapter for FrictionBacktester and ML feature code."""
        return self.bars.copy(deep=True)


def build_dataset(raw: pd.DataFrame, *, spec: BarSpec, start: datetime,
                  end: datetime, calendar: SessionCalendar | None,
                  retrieved_at: datetime | None = None, strict: bool = True) -> Dataset:
    """Create validated offline data; `strict=False` is diagnostic-only."""
    left, right = utc_stamp(start, "start"), utc_stamp(end, "end")
    observed = utc_stamp(retrieved_at or datetime.now(timezone.utc), "retrieved_at")
    bars = normalize_bars(raw, spec)
    report = audit_bars(bars, spec, start=left, end=right, calendar=calendar)
    if strict and not report.passed:
        raise DataQualityError(f"market data quality failed: {asdict(report)}")
    digest = _frame_digest(bars)
    spec_dict = asdict(spec)
    window_start, window_end = left.isoformat(), right.isoformat()
    identifier = _version_id(digest, spec_dict, window_start, window_end)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": identifier,
        "content_sha256": digest,
        "spec": spec_dict,
        "requested_start_utc": window_start,
        "requested_end_utc": window_end,
        "retrieved_at_utc": observed.isoformat(),
        "adjustment_claim": "source/request metadata only; corporate actions not independently verified",
        "quality": asdict(report),
        "row_count": len(bars),
    }
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    return Dataset(bars=bars, manifest=manifest, quality=report)


def write_dataset(dataset: Dataset, root: Path) -> Path:
    """Store only quality-passing snapshots; existing IDs are never overwritten."""
    if not dataset.quality.passed:
        raise DataQualityError("cannot publish an unverified/invalid dataset")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    identifier = dataset.manifest["dataset_id"]
    if not isinstance(identifier, str) or len(identifier) != 24 or not all(c in "0123456789abcdef" for c in identifier):
        raise ValueError("invalid dataset ID")
    path = root / f"{identifier}.parquet"
    sidecar = root / f"{identifier}.manifest.json"
    if path.exists() or sidecar.exists():
        raise FileExistsError("immutable snapshot already exists")
    with TemporaryDirectory(dir=root, prefix=".quant_data_") as staging:
        staged = Path(staging)
        dataset.bars.to_parquet(staged / "bars.parquet", index=True)
        (staged / "manifest.json").write_text(
            json.dumps(dataset.manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(staged / "bars.parquet", path)
        os.replace(staged / "manifest.json", sidecar)
    return path


def read_dataset(root: Path, dataset_id: str, *, max_age: timedelta | None = None,
                 now: datetime | None = None, require_quality: bool = True) -> Dataset:
    """Fail closed on schema mismatch, absent manifest, staleness or tampering."""
    if len(dataset_id) != 24 or any(ch not in "0123456789abcdef" for ch in dataset_id):
        raise ValueError("invalid dataset ID")
    root = Path(root)
    manifest = json.loads((root / f"{dataset_id}.manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != _manifest_digest(manifest):
        raise DataQualityError("snapshot manifest integrity check failed")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("dataset_id") != dataset_id:
        raise DataQualityError("unknown schema or mismatched dataset ID")
    if max_age is not None:
        current = utc_stamp(now or datetime.now(timezone.utc), "now")
        age = current - utc_stamp(manifest["retrieved_at_utc"], "retrieved_at")
        if max_age < timedelta(0) or age < timedelta(0) or age > max_age:
            raise StaleDatasetError(f"dataset provenance is stale: age={age}")
    spec = BarSpec(**manifest["spec"])
    bars = normalize_bars(pd.read_parquet(root / f"{dataset_id}.parquet"), spec)
    quality = QualityReport(**manifest["quality"])
    digest = _frame_digest(bars)
    expected_id = _version_id(digest, manifest["spec"], manifest["requested_start_utc"], manifest["requested_end_utc"])
    if (digest != manifest["content_sha256"] or expected_id != dataset_id
            or len(bars) != manifest["row_count"]):
        raise DataQualityError("snapshot data/manifest integrity check failed")
    if require_quality and not quality.passed:
        raise DataQualityError("snapshot does not have a passing quality report")
    return Dataset(bars=bars, manifest=manifest, quality=quality)
