"""E1.13 actual separate-process audit contention (disposable local SQLite).

No Alpaca, Supabase, orders or external connections. This verifies only the
local audit serialization boundary, NOT the distributed database+broker lease.
"""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from execution_audit import ExecutionAuditStore, AuditIntegrityError


ROOT = Path(__file__).resolve().parents[1]


def _launch(script, path, *args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.Popen(
        [sys.executable, "-c", script, str(path), *map(str, args)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_two_processes_cannot_create_same_durable_baseline(tmp_path):
    path = tmp_path / "shared.sqlite3"
    ExecutionAuditStore(path)
    script = """
import sys
from execution_audit import ExecutionAuditStore, AuditIntegrityError
store = ExecutionAuditStore(sys.argv[1], require_existing=True)
try:
    store.append(101, 'e1-101', 'pre_submit_baseline',
                 {'client_order_id':'e1-101','position_qty':'0'})
except AuditIntegrityError as exc:
    print(type(exc).__name__)
    sys.exit(13)
print('recorded')
"""
    left = _launch(script, path)
    right = _launch(script, path)
    results = [left.communicate(timeout=20), right.communicate(timeout=20)]
    codes = [left.returncode, right.returncode]
    assert sorted(codes) == [0, 13], results
    store = ExecutionAuditStore(path, require_existing=True)
    assert store.verify()["event_count"] == 1
    assert store.baseline(101)["position_qty"] == "0"


def test_parallel_processes_preserve_global_hash_chain(tmp_path):
    path = tmp_path / "shared.sqlite3"
    ExecutionAuditStore(path)
    script = """
import sys
from execution_audit import ExecutionAuditStore
command_id = int(sys.argv[2])
ExecutionAuditStore(sys.argv[1], require_existing=True).append(
    command_id, f'e1-{command_id}', 'manual_review',
    {'process':str(command_id)},
)
"""
    workers = [_launch(script, path, 201 + i) for i in range(5)]
    for worker in workers:
        out, err = worker.communicate(timeout=20)
        assert worker.returncode == 0, (worker.returncode, out, err)
    store = ExecutionAuditStore(path, require_existing=True)
    assert store.verify()["event_count"] == 5
    for i in range(5):
        assert len(store.history(201 + i)) == 1


def test_disappearing_database_is_not_silently_recreated(tmp_path):
    path = tmp_path / "audit.sqlite3"
    store = ExecutionAuditStore(path)
    store.append(101, "e1-101", "pre_submit_baseline",
                 {"client_order_id": "e1-101", "position_qty": "0"})
    path.unlink()
    with pytest.raises(AuditIntegrityError, match="disappeared"):
        store.verify()
    assert not path.exists()
    with pytest.raises(AuditIntegrityError, match="Existing"):
        ExecutionAuditStore(path, require_existing=True)
    assert not path.exists()


def test_corrupted_audit_file_is_not_trusted_for_restart(tmp_path):
    path = tmp_path / "audit.sqlite3"
    ExecutionAuditStore(path)
    path.write_bytes(b"not a SQLite database")
    with pytest.raises((AuditIntegrityError, sqlite3.DatabaseError)):
        ExecutionAuditStore(path, require_existing=True)
