"""E1.8: local temporary SQLite only; never access Alpaca or Supabase."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from execution_audit import ExecutionAuditStore, AuditIntegrityError
from live_paper_trader import LiveExecutionEngine


def store(tmp_path):
    return ExecutionAuditStore(tmp_path / "e1-audit.sqlite3")


def test_append_reopen_verify_and_replay(tmp_path):
    first = store(tmp_path)
    a = first.append(101, "e1-101", "pre_submit_baseline", {"symbol": "SPY", "qty": "0"})
    b = first.append(101, "e1-101", "broker_order_snapshot", {"filled_qty": "2"})
    assert a["sequence"] == 1 and b["sequence"] == 2
    reopened = store(tmp_path)
    assert reopened.verify()["event_count"] == 2
    assert reopened.baseline(101)["qty"] == "0"
    assert [x["kind"] for x in reopened.history(101)] == [
        "pre_submit_baseline", "broker_order_snapshot",
    ]


def test_mutated_payload_detected_on_reopen(tmp_path):
    item = store(tmp_path)
    item.append(101, "e1-101", "pre_submit_baseline", {"qty": "0"})
    with sqlite3.connect(str(item.path)) as db:
        db.execute("UPDATE events SET payload=? WHERE seq=1", ('{"qty":"500"}',))
    with pytest.raises(AuditIntegrityError, match="hash mismatch"):
        store(tmp_path)


def test_deleted_middle_entry_detected(tmp_path):
    item = store(tmp_path)
    for n in range(3):
        item.append(101, "e1-101", "manual_review", {"n": n})
    with sqlite3.connect(str(item.path)) as db:
        db.execute("DELETE FROM events WHERE seq=2")
    with pytest.raises(AuditIntegrityError, match="sequence"):
        item.append(101, "e1-101", "manual_review", {"n": 4})


def test_invalid_id_or_kind_rejected(tmp_path):
    item = store(tmp_path)
    with pytest.raises(AuditIntegrityError):
        item.append(101, "e1-999", "manual_review", {})
    with pytest.raises(AuditIntegrityError):
        item.append(101, "e1-101", "broker_submit", {})
    with pytest.raises(AuditIntegrityError):
        item.append(True, "e1-True", "manual_review", {})


def test_nan_or_non_json_rejected(tmp_path):
    item = store(tmp_path)
    with pytest.raises(AuditIntegrityError):
        item.append(101, "e1-101", "portfolio_snapshot", {"qty": float("nan")})
    with pytest.raises(AuditIntegrityError):
        item.append(101, "e1-101", "portfolio_snapshot", {"unsafe": object()})


def test_duplicate_baselines_fail_closed(tmp_path):
    item = store(tmp_path)
    item.append(101, "e1-101", "pre_submit_baseline", {"qty": "0"})
    item.append(101, "e1-101", "pre_submit_baseline", {"qty": "1"})
    with pytest.raises(AuditIntegrityError, match="Exactly one"):
        item.baseline(101)


def test_missing_baseline_rejected(tmp_path):
    with pytest.raises(AuditIntegrityError, match="Exactly one"):
        store(tmp_path).baseline(101)


def test_concurrent_writers_get_unique_sequence(tmp_path):
    path = tmp_path / "e1-audit.sqlite3"
    store(tmp_path)
    def record(i):
        return ExecutionAuditStore(path).append(
            101, "e1-101", "manual_review", {"iteration": i},
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(record, range(12)))
    assert sorted(x["sequence"] for x in results) == list(range(1, 13))
    assert ExecutionAuditStore(path).verify()["event_count"] == 12


def test_worker_records_evidence_without_broker_or_database_calls(tmp_path):
    worker = LiveExecutionEngine.__new__(LiveExecutionEngine)
    worker.trading_client = MagicMock()
    path = tmp_path / "worker.sqlite3"
    worker.record_reconciliation_evidence(
        path, 101, "e1-101", baseline={"symbol": "SPY", "quantity": "0"},
    )
    worker.record_reconciliation_evidence(
        path, 101, "e1-101", broker_order={"status": "filled", "filled_qty": "4"},
    )
    assert ExecutionAuditStore(path).verify()["event_count"] == 2
    worker.trading_client.submit_order.assert_not_called()
    worker.trading_client.get_order_by_client_id.assert_not_called()


def test_worker_refuses_snapshot_without_persistent_baseline(tmp_path):
    worker = LiveExecutionEngine.__new__(LiveExecutionEngine)
    with pytest.raises(AuditIntegrityError, match="baseline"):
        worker.record_reconciliation_evidence(
            tmp_path / "missing.sqlite3", 101, "e1-101",
            broker_order={"filled_qty": "2"},
        )


def test_worker_refuses_second_baseline(tmp_path):
    worker = LiveExecutionEngine.__new__(LiveExecutionEngine)
    path = tmp_path / "second.sqlite3"
    worker.record_reconciliation_evidence(
        path, 101, "e1-101", baseline={"qty": "0"},
    )
    with pytest.raises(AuditIntegrityError, match="precede"):
        worker.record_reconciliation_evidence(
            path, 101, "e1-101", baseline={"qty": "1"},
        )


def test_relative_path_rejected():
    with pytest.raises(AuditIntegrityError, match="absolute"):
        ExecutionAuditStore(Path("state/local.sqlite3"))
