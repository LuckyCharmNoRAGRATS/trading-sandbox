"""Quant AI market-data snapshot contract v1 (offline, broker-independent)."""
from .calendar import Session, SessionCalendar
from .quality import audit_bars, normalize_bars
from .schema import BarSpec, DataQualityError, QualityReport, SCHEMA_VERSION, StaleDatasetError
from .storage import Dataset, build_dataset, read_dataset, write_dataset

__all__ = [
    "Session", "SessionCalendar", "BarSpec", "DataQualityError", "QualityReport",
    "StaleDatasetError", "SCHEMA_VERSION", "Dataset", "normalize_bars",
    "audit_bars", "build_dataset", "read_dataset", "write_dataset",
]
