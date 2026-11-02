"""Independent offline quantitative research (no broker imports)."""
from .core import ExperimentSpec, make_signals, run_experiment, validate_bars, write_report

__all__ = ["ExperimentSpec", "make_signals", "run_experiment", "validate_bars", "write_report"]
