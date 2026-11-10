# Read-only paper-account evidence

This standalone Python 3.9+ collector uses the standard library and never imports the trader, dotenv, dashboard, or order-capable SDK. The endpoint is fixed to Alpaca paper trading. Only GET requests to an allowlist are possible; redirects are refused.

Supply current paper credentials through the process environment (`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`). Do not use credentials recovered from Git history. No credentials are read from the tracked .env file.

From the repository root, after provisioning the environment:

```bash
python tools/collect_paper_snapshot.py --date 2026-11-04 --output /tmp/quant-paper-2026-11-04.json
```

The output is private (0600), must not already exist, and must not be committed or publicly uploaded. Exit 0 means requested collection sections succeeded; exit 2 means missing credentials, invalid inputs, an incomplete section, or another collection failure. Inspect section statuses before analysis. Raw broker data includes account identifiers.

Collected evidence:
- Current account before and after collection, and current positions.
- Current open orders and orders submitted during the requested New York date, with a one-second overlap at the lower boundary to avoid losing exact-midnight orders. Downstream analysis must apply the exact reporting window.
- All activity types for the requested broker activity date, paginated by activity ID.
- Individual orders referenced by activities, including orders submitted earlier.
- Daily portfolio history for the requested date.

A 500-order page is marked incomplete. The collector does not silently advance a timestamp cursor and lose equal-timestamp orders. Activity pagination rejects duplicate records, repeated pages and exhaustion of the page budget. API errors remain visible as failed sections, without recording response bodies or exception messages.

This is evidence collection, not reconciliation. Current positions are not historical closing positions. An empty activity list is not proof of zero P&L. Opening inventory, cost basis, cash movements and fees must be reconciled separately. Sequential reads are not atomic, and matching account values cannot establish stable positions/orders. No output authorizes execution or releases blocked commands.

Validation on 2026-11-10: 15 standalone offline tests passed. No broker credentials were present in the work environment; no broker requests, VM restart, trade, merge, or production deployment was performed. The existing trader and strategy files are unchanged.

```bash
python -m unittest discover -s tests -p test_paper_evidence_collector.py -v
```

API references:
- https://docs.alpaca.markets/us/reference/getaccountactivities-2
- https://docs.alpaca.markets/us/reference/getallorders-1
- https://docs.alpaca.markets/us/reference/getaccountportfoliohistory-1

## Offline evidence audit

```bash
python tools/audit_paper_snapshot.py /tmp/quant-paper-2026-11-04.json
```

This command performs no network calls. It prints JSON with a SHA-256 of the input, section-coverage issues, account identity/equity drift, and per-symbol fill quantities and trade cash flow excluding fees. It checks unique activity IDs, timezone-aware fill dates in New York, linked-order symbol/side, and daily fill quantities against lifetime order filled quantities. A day's fills may legitimately be less than lifetime filled quantity.

Exit 0 means only the implemented evidence checks passed, not that all broker fields have been independently validated or accounting reconciled. Exit 2 means one or more checks failed or the input could not be read. Non-fill activity types require separate cash-movement review. Missing coverage or malformed fills must not be interpreted as zero trades or zero profit. Realized and unrealized P&L remain null, and execution authorization remains false. Timestamp freshness, historical inventory reconstruction, fees, and lot-level cost basis reconciliation remain future work.

Validation: 26 collector/auditor offline tests passed locally. Dedicated GitHub Actions workflow added; its remote result is separate from local results.
