"""E1.8 local, durable, tamper-evident execution evidence.

SQLite transactions + hash chaining detect changed/reordered interior records.
A local hash chain alone CANNOT detect malicious suffix truncation or a full
database rewrite; independent signed/WORM checkpoints are a future release gate.
Never put credentials or customer secrets in evidence payloads.
No Alpaca/Supabase calls and absolutely no trade-clearance functionality.
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class AuditIntegrityError(RuntimeError):
    pass


GENESIS = "0" * 64
ALLOWED_KINDS = frozenset({
    "command_claimed", "pre_submit_baseline", "submission_uncertain",
    "broker_order_snapshot", "portfolio_snapshot", "reconciliation_report",
    "manual_review",
})


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AuditIntegrityError("Evidence is not canonical JSON") from exc


def _digest(sequence, timestamp, command_id, client_order_id, kind, payload, previous_hash):
    blob = _canonical({
        "seq": sequence, "ts": timestamp, "command_id": command_id,
        "client_order_id": client_order_id, "kind": kind,
        "payload": payload, "previous": previous_hash,
    })
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ExecutionAuditStore:
    """Explicit opt-in local storage; caller must supply persistent disk path."""

    def __init__(self, path):
        self.path = Path(path)
        if self.path == Path(":memory:") or not self.path.is_absolute():
            raise AuditIntegrityError("Audit database requires an absolute persistent path")
        if self.path.is_symlink() or not self.path.parent.is_dir():
            raise AuditIntegrityError("Missing or unsafe audit database directory")
        if self.path.exists() and not self.path.is_file():
            raise AuditIntegrityError("Audit database is not a regular file")
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                command_id INTEGER NOT NULL,
                client_order_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS audit_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                initial_version INTEGER NOT NULL
            )""")
            conn.execute("INSERT OR IGNORE INTO audit_meta VALUES(1, 1)")
            self._verify_conn(conn)

    def _verify_conn(self, conn):
        meta = conn.execute("SELECT initial_version FROM audit_meta WHERE singleton=1").fetchall()
        if meta != [(1,)]:
            raise AuditIntegrityError("Audit metadata missing or corrupt")
        prev = GENESIS
        expected_seq = 1
        rows = conn.execute(
            "SELECT seq,timestamp,command_id,client_order_id,kind,payload,previous_hash,event_hash "
            "FROM events ORDER BY seq"
        )
        for seq, timestamp, command_id, client_id, kind, serialized, before, actual in rows:
            if seq != expected_seq or before != prev or kind not in ALLOWED_KINDS:
                raise AuditIntegrityError("Audit sequence or chain corrupted")
            try:
                payload = json.loads(serialized)
            except (TypeError, ValueError) as exc:
                raise AuditIntegrityError("Audit evidence malformed") from exc
            if _canonical(payload) != serialized:
                raise AuditIntegrityError("Audit payload changed encoding")
            expected = _digest(seq, timestamp, command_id, client_id, kind, payload, before)
            if actual != expected:
                raise AuditIntegrityError("Audit event hash mismatch")
            prev = actual
            expected_seq += 1
        return expected_seq, prev

    def verify(self):
        with self._connect() as conn:
            sequence, digest = self._verify_conn(conn)
            return {"event_count": sequence - 1, "head_hash": digest}

    def append(self, command_id, client_order_id, kind, payload):
        if not isinstance(command_id, int) or isinstance(command_id, bool) or command_id <= 0:
            raise AuditIntegrityError("Invalid command identity")
        if client_order_id != f"e1-{command_id}":
            raise AuditIntegrityError("Client order ID does not belong to command")
        if kind not in ALLOWED_KINDS:
            raise AuditIntegrityError("Unrecognized evidence kind")
        serialized = _canonical(payload)
        if not isinstance(payload, dict) or len(serialized) > 32768:
            raise AuditIntegrityError("Evidence must be a small object")
        timestamp = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            seq, prev = self._verify_conn(conn)
            if kind == "pre_submit_baseline":
                existing = conn.execute(
                    "SELECT count(*) FROM events WHERE command_id=? AND kind='pre_submit_baseline'",
                    (command_id,),
                ).fetchone()[0]
                if existing:
                    raise AuditIntegrityError("Duplicate pre-submit baseline")
            digest = _digest(seq, timestamp, command_id, client_order_id, kind, payload, prev)
            conn.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                (seq, timestamp, command_id, client_order_id, kind, serialized, prev, digest),
            )
            conn.execute("COMMIT")
            return {"sequence": seq, "event_hash": digest}
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def history(self, command_id):
        if not isinstance(command_id, int) or isinstance(command_id, bool) or command_id <= 0:
            raise AuditIntegrityError("Invalid command identity")
        with self._connect() as conn:
            self._verify_conn(conn)
            rows = conn.execute(
                "SELECT seq,kind,payload,event_hash FROM events WHERE command_id=? ORDER BY seq",
                (command_id,),
            ).fetchall()
            return [
                {"sequence": seq, "kind": kind, "payload": json.loads(payload), "event_hash": digest}
                for seq, kind, payload, digest in rows
            ]

    def baseline(self, command_id):
        """A duplicated baseline or no baseline is never silently trusted."""
        events = [e for e in self.history(command_id) if e["kind"] == "pre_submit_baseline"]
        if len(events) != 1:
            raise AuditIntegrityError("Exactly one pre-submit baseline required")
        return events[0]["payload"]
