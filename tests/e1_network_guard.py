"""Fail-closed network guard for isolated E1 GitHub Actions pytest runs.

Loaded via pytest -p before tests are collected. Does not affect application
runtime or a normal local pytest invocation.
"""
import os
import socket
import dotenv

# Prevent tracked dotenv contents and inherited credentials from reaching tests.
dotenv.load_dotenv = lambda *args, **kwargs: False
for key in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY", "DISCORD_WEBHOOK_URL", "E1_RELEASE_APPROVED",
            "E1_BROKER_MODE", "E1_AUDIT_DB_PATH"):
    os.environ.pop(key, None)
os.environ["E1_EXECUTION_ENABLED"] = "false"
os.environ["E1_RECONCILIATION_ENABLED"] = "false"


def _forbidden_network(*args, **kwargs):
    raise AssertionError("E1 CI forbids all socket network connections")


socket.socket.connect = _forbidden_network
socket.socket.connect_ex = _forbidden_network
socket.create_connection = _forbidden_network

