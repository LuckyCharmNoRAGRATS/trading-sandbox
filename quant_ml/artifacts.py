"""Explicit local artifact export: never commit models or auto-load pickles."""
import hashlib
import json
import platform
from pathlib import Path
import joblib
import sklearn
from .contracts import SCHEMA_VERSION, FEATURE_COLUMNS
from .research import ResearchModel


def save_local_artifact(model: ResearchModel, directory: Path, metrics: list[dict]) -> tuple[Path, Path]:
    """Export only to an explicit destination. Never loads or executes a saved model."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / f"research_{model.model_version}.joblib"
    manifest_path = directory / f"research_{model.model_version}.json"
    if model_path.exists() or manifest_path.exists():
        raise FileExistsError("versioned artifact already exists")
    try:
        joblib.dump(model, model_path)
        fingerprint = hashlib.sha256(model_path.read_bytes()).hexdigest()
        manifest = {"schema_version": SCHEMA_VERSION, "model_version": model.model_version,
                    "training_digest_sha256": model.training_digest,
                    "artifact_sha256": fingerprint, "feature_columns": list(FEATURE_COLUMNS),
                    "python_version": platform.python_version(),
                    "sklearn_version": sklearn.__version__, "metrics": metrics,
                    "execution_status": "RESEARCH_ONLY"}
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        model_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise
    return model_path, manifest_path
