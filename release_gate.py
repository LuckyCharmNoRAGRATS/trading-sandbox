"""E1.11 conservative runtime release gate.

E1_EXECUTION_ENABLED alone must never authorize an execution worker.
This module only validates configuration; it does not contact any service.
Environment flags are not a substitute for independent production approval.
"""
import os
from pathlib import Path


class ReleaseGateError(RuntimeError):
    pass


def validate_execution_release(env=None):
    """Fail closed before DB/broker initialization or any order-capable calls.

    The default-disabled path is intentionally a non-executing result.
    Both flags must be exact lower-case literals; no truthy coercion.
    """
    cfg = os.environ if env is None else env
    enabled = cfg.get("E1_EXECUTION_ENABLED")
    if enabled != "true":
        return {"execution_allowed": False, "reason": "execution disabled"}

    if cfg.get("E1_RELEASE_APPROVED") != "paper-only-reviewed":
        raise ReleaseGateError("Independent paper-only release approval absent")
    if cfg.get("E1_BROKER_MODE") != "paper":
        raise ReleaseGateError("Paper broker mode not explicitly locked")
    if cfg.get("E1_RECONCILIATION_ENABLED") != "true":
        raise ReleaseGateError("Recovery reconciliation must be explicitly enabled")

    audit_path = cfg.get("E1_AUDIT_DB_PATH")
    if not isinstance(audit_path, str) or not audit_path:
        raise ReleaseGateError("Persistent E1 audit database path missing")
    path = Path(audit_path)
    if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir():
        raise ReleaseGateError("Audit database path is not a safe absolute path")
    if not path.exists() or not path.is_file():
        raise ReleaseGateError("Existing verified audit database required")
    return {"execution_allowed": True, "reason": "configuration preconditions met only"}
