"""Research-only inference payloads. No signal/order/veto booleans."""
from dataclasses import dataclass
from typing import Mapping
import numpy as np
import pandas as pd
from .contracts import FEATURE_COLUMNS, SCHEMA_VERSION
from .research import ResearchModel


@dataclass(frozen=True)
class ResearchInferenceRequest:
    symbol: str
    asof_utc: pd.Timestamp
    model_version: str
    features: Mapping[str, float]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class ResearchInferenceResult:
    symbol: str
    asof_utc: str
    model_version: str
    probability_profit_target: float
    use: str = "RESEARCH_ONLY"


def predict_research_only(model: ResearchModel, request: ResearchInferenceRequest) -> ResearchInferenceResult:
    ts = pd.Timestamp(request.asof_utc)
    if not request.symbol.strip() or ts.tzinfo is None or str(ts.tz) != "UTC":
        raise ValueError("symbol and UTC asof are required")
    if request.schema_version != SCHEMA_VERSION or request.model_version != model.model_version:
        raise ValueError("schema/model version mismatch")
    if set(request.features) != set(FEATURE_COLUMNS):
        raise ValueError("missing or extra feature keys")
    vals = np.array([request.features[k] for k in FEATURE_COLUMNS], dtype=float)
    if not np.isfinite(vals).all():
        raise ValueError("nonfinite inference feature")
    frame = pd.DataFrame([vals], columns=list(FEATURE_COLUMNS), index=pd.DatetimeIndex([ts]))
    probability = float(model.predict_probability(frame)[0])
    return ResearchInferenceResult(request.symbol.upper(), ts.isoformat(),
                                   model.model_version, probability)
