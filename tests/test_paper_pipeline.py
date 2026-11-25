"""Synthetic collector -> private file -> audit CLI contract tests."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load("collect_paper_snapshot")
auditor = load("audit_paper_snapshot")
OID = "12345678-1234-1234-1234-123456789012"


class SyntheticBroker:
    """Only a GET interface; no real credentials or external service."""
    def __init__(self, broken_history=False, capped_orders=False):
        self.broken_history = broken_history
        self.capped_orders = capped_orders

    def get(self, path, params=None):
        order = {"id": OID, "symbol": "SPY", "side": "buy", "filled_qty": "0.3"}
        if path == "/v2/account":
            return {"id": "synthetic-account", "equity": "100000"}
        if path == "/v2/positions":
            return [{"symbol": "SPY", "qty": "0.3"}]
        if path == "/v2/orders":
            if self.capped_orders:
                return [dict(order, id=str(i)) for i in range(500)]
            return [] if params["status"] == "open" else [order]
        if path == "/v2/orders/" + OID:
            return order
        if path == "/v2/account/activities":
            return [
                {"id": "fill-a", "activity_type": "FILL", "order_id": OID,
                 "symbol": "SPY", "side": "buy", "qty": "0.1", "price": "100.10",
                 "transaction_time": "2026-11-04T15:00:00Z"},
                {"id": "fill-b", "activity_type": "FILL", "order_id": OID,
                 "symbol": "SPY", "side": "buy", "qty": "0.2", "price": "100.20",
                 "transaction_time": "2026-11-04T15:01:00Z"},
            ]
        if path == "/v2/account/portfolio/history":
            if self.broken_history:
                raise RuntimeError("private broker response must not escape")
            epoch = auditor.datetime(2026, 11, 4, 21, tzinfo=auditor.timezone.utc).timestamp()
            return {"timestamp": [epoch], "equity": [100000]}
        raise AssertionError("Unexpected endpoint")


class PipelineTests(unittest.TestCase):
    def run_cli(self, report):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            collector.save(report, path)
            raw = path.read_bytes()
            run = subprocess.run(
                [sys.executable, str(ROOT / "tools/audit_paper_snapshot.py"), str(path)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(path.read_bytes(), raw, "Auditing must not change evidence")
            return run, hashlib.sha256(raw).hexdigest()

    def test_complete_collection_round_trip_and_cli_hash(self):
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            report = collector.collect(SyntheticBroker(), "2026-11-04")
            self.assertTrue(report["collection_ok"])
            result = auditor.audit(report)
        self.assertTrue(result["evidence_checks_passed"], result["issues"])
        self.assertEqual(result["fill_summary"]["SPY"]["buy_qty"], "0.3")
        self.assertEqual(result["fill_summary"]["SPY"]["trade_cash_flow_ex_fees"], "-30.050")
        run, digest = self.run_cli(report)
        self.assertEqual(run.returncode, 0, run.stderr)
        output = json.loads(run.stdout)
        self.assertEqual(output["source_sha256"], digest)
        self.assertIsNone(output["realized_pnl"])
        self.assertFalse(output["reconciled"])
        self.assertFalse(output["execution_authorized"])

    def test_broker_failure_survives_file_and_returns_failure(self):
        report = collector.collect(SyntheticBroker(broken_history=True), "2026-11-04")
        self.assertFalse(report["collection_ok"])
        self.assertNotIn("private broker response", json.dumps(report))
        run, _ = self.run_cli(report)
        self.assertEqual(run.returncode, 2)
        self.assertIn("missing_or_failed_section:portfolio_history", json.loads(run.stdout)["issues"])

    def test_capped_orders_cannot_become_complete_in_audit(self):
        report = collector.collect(SyntheticBroker(capped_orders=True), "2026-11-04")
        run, _ = self.run_cli(report)
        self.assertEqual(run.returncode, 2)
        self.assertFalse(json.loads(run.stdout)["evidence_checks_passed"])

    def test_stale_export_cli_rejects_even_if_historical_day_is_valid(self):
        with patch.object(collector, "now", return_value="2000-01-01T00:00:00+00:00"):
            report = collector.collect(SyntheticBroker(), "2026-11-04")
        run, _ = self.run_cli(report)
        self.assertEqual(run.returncode, 2)
        self.assertIn("stale_snapshot", json.loads(run.stdout)["issues"])

    def test_invalid_json_cli_fails_without_traceback_or_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            path.write_text("PRIVATE-BROKER-DATA")
            run = subprocess.run(
                [sys.executable, str(ROOT / "tools/audit_paper_snapshot.py"), str(path)],
                capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(run.returncode, 2)
        self.assertNotIn("PRIVATE-BROKER-DATA", run.stderr)
        self.assertNotIn("Traceback", run.stderr)


if __name__ == "__main__":
    unittest.main()
