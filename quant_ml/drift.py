"""Population stability index with reference-only bin edges."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .contracts import FEATURE_COLUMNS, validate_features


@dataclass(frozen=True)
class DriftBaseline:
    edges: dict
    reference_proportions: dict


def build_drift_baseline(training_features: pd.DataFrame, bins: int = 10) -> DriftBaseline:
    validate_features(training_features)
    if len(training_features) < 30 or bins < 2:
        raise ValueError("at least 30 reference rows and two bins required")
    edges, proportions = {}, {}
    for column in FEATURE_COLUMNS:
        values = training_features[column].to_numpy(dtype=float)
        thresholds = np.unique(np.quantile(values, np.linspace(0, 1, bins + 1)[1:-1]))
        boundaries = np.r_[-np.inf, thresholds, np.inf]
        counts, _ = np.histogram(values, bins=boundaries)
        edges[column] = boundaries
        proportions[column] = counts / counts.sum()
    return DriftBaseline(edges, proportions)


def report_population_stability(baseline: DriftBaseline, recent: pd.DataFrame) -> dict[str, float]:
    validate_features(recent)
    if len(recent) < 30:
        raise ValueError("at least 30 recent rows required")
    output = {}
    for column in FEATURE_COLUMNS:
        actual_counts, _ = np.histogram(recent[column].to_numpy(dtype=float),
                                        bins=baseline.edges[column])
        actual = np.clip(actual_counts / len(recent), 1e-6, 1)
        expected = np.clip(baseline.reference_proportions[column], 1e-6, 1)
        output[column] = float(np.sum((actual - expected) * np.log(actual / expected)))
    return output
