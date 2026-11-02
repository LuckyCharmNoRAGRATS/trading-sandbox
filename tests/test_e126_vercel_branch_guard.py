"""E1.26: static-only regression contract; no Vercel API, network or deployments."""
from __future__ import annotations

import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "vercel.json"
PROTECTED = "feature/e116-congress-readiness"


def _config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_branch_specific_auto_deployment_disabled():
    rules = _config()["git"]["deploymentEnabled"]
    assert rules == {PROTECTED: False}
    assert rules[PROTECTED] is False


def test_other_branches_not_disabled():
    rules = _config()["git"]["deploymentEnabled"]
    assert isinstance(rules, dict)
    assert "main" not in rules
    assert "feature/quant-data" not in rules
    assert not any(k in {"*", "feature/*", "**"} for k in rules)


def test_dashboard_rewrites_preserved():
    config = _config()
    assert config["version"] == 2
    assert config["cleanUrls"] is True
    assert config["rewrites"] == [{"source": "/(.*)", "destination": "/index.html"}]


def test_no_cron_secrets_or_external_functions():
    config = _config()
    assert "crons" not in config
    assert "functions" not in config
    assert "env" not in config
    assert "buildCommand" not in config
    assert set(config) == {"version", "cleanUrls", "rewrites", "git"}


def test_trading_and_sync_not_unblocked_by_config():
    text = CONFIG.read_text(encoding="utf-8").casefold()
    for forbidden in ("supabase", "alpaca", "scheduler", "cron", "trade", 'deploymentenabled": true'):
        assert forbidden not in text
