"""E1.22 manual, one-shot, read-only Tracefour connectivity verification.

The default action is OFFLINE and can only inspect a saved JSON file. A live
GET requires *two* explicit flags, and is NEVER run by tests or CI. The fixed
URL, no redirect policy, zero HTTP retries and small response cap are supplied
by E1.21's probe_once. This command never persists a raw response, reads any
secrets, imports Supabase/Alpaca or changes an execution flag.

Approval must be obtained from the user before running a live invocation.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

from congress_connection_diagnostic import (
    DiagnosticError,
    diagnose_saved_json,
    probe_once,
)


class ProbeConfigurationError(RuntimeError):
    """Only an explicitly approved single-request probe is permitted."""


def make_no_retry_session() -> Any:
    """Create an HTTP session. No network I/O occurs during construction."""
    import requests  # Deliberately imported only after opt-in selection.
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=0, connect=0, read=0, status=0, redirect=0,
        backoff_factor=0, raise_on_redirect=False,
    )))
    return session


def run_manual_probe(*, approved: bool, session_factory: Callable[[], Any],
                     now: datetime | None = None) -> dict[str, Any]:
    """Perform ONE injected-session GET *only* when explicitly approved.

    This is an opt-in utility for a future user-authorized run on their own
    computer or private backend, not approval to activate an automated job.
    """
    if approved is not True:
        raise ProbeConfigurationError("A new explicit one-GET approval is required")
    session = session_factory()
    try:
        result = probe_once(session, now=now)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()
    result["invocation_mode"] = "explicit_manual_single_get"
    result["scheduled_sync_enabled"] = False
    result["approved_for_ingestion"] = False
    result["approved_for_trading"] = False
    return result


def main(argv: list[str] | None = None, *,
         session_factory: Callable[[], Any] | None = None,
         now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline Tracefour inspection or explicitly approved SINGLE read-only GET; never ingests or trades")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline-file", type=Path,
                      help="inspect saved JSON only, without any network activity")
    mode.add_argument("--one-get", action="store_true",
                      help="opt-in to one manual GET; requires --i-approve-one-get")
    parser.add_argument("--i-approve-one-get", action="store_true",
                        help="acknowledge separate explicit approval for this invocation only")
    args = parser.parse_args(argv)
    if args.offline_file is not None and args.i_approve_one_get:
        parser.error("Live-request approval cannot accompany offline inspection")
    if args.one_get and not args.i_approve_one_get:
        parser.error("--one-get requires --i-approve-one-get and separate user approval")
    try:
        if args.offline_file is not None:
            report = diagnose_saved_json(args.offline_file, now=now)
            report["invocation_mode"] = "offline_saved_file_only"
        else:
            # No session is instantiated before both explicit flags are checked.
            report = run_manual_probe(
                approved=args.i_approve_one_get,
                session_factory=session_factory or make_no_retry_session,
                now=now,
            )
    except (DiagnosticError, ProbeConfigurationError) as exc:
        parser.exit(2, f"PROBE FAILED: {exc}\n")
    print(json.dumps(report, sort_keys=True, indent=2))
    # Nonzero exit codes distinguish broken transport from poor data quality.
    if report.get("transport_status") in {
        "failed_before_http_response", "invalid_http_response", "redirect_blocked",
        "http_error", "unsupported_content_type", "invalid_body",
    }:
        return 3
    if report.get("quality_status") == "blocked":
        return 2
    # A success means only source JSON structure, never ingestion clearance.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
