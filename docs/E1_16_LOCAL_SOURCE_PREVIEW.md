# E1.16 — Retrieve and validate one saved Pelosi API response

**State:** Awaiting a user-supplied JSON file. Approval A's earlier direct HTTP GET failed with DNS resolution in the assistant environment. No second API request has been issued. Approval B (database ingestion) and Approval C (schedule) are NOT authorized.

## One-time data retrieval on Windows PowerShell

Run **on your own computer**, after deciding to make the request:

```powershell
curl.exe --fail --silent --show-error --connect-timeout 10 --max-time 30 "https://tracefour.com/v1/congress/nancy-pelosi" --output "$env:USERPROFILE\Downloads\tracefour_pelosi.json"
```

This command requests the **Congress member transactions** endpoint, not the separate reconstructed portfolio tracker. It saves a local file and makes no Supabase or broker call. If it fails, **do not** substitute an invented response or silently switch to another provider. Capture the terminal error and report it; the DNS issue may exist on the user's network as well.

Upload `tracefour_pelosi.json` to the ChatGPT conversation. It should only contain the public Tracefour JSON response; do not include API keys or credentials.

## Offline checks: no network, no database, no broker

On the isolated `feature/e116-congress-readiness` branch:

```powershell
python congress_preview.py "$env:USERPROFILE\Downloads\tracefour_pelosi.json"
```

This script reads local bytes only, validates the response with the **current UTC time**, verifies source freshness, member identity and HTTPS filing links, rejects invalid or duplicated disclosures and potentially truncated 500-record responses, and reports asset classes, ownership, reporting lag and sample filing URLs.

Unlike `congress_ingest.py --offline-fixture`, this is **NOT** historical fixture mode: it cannot pretend a months-old source response is fresh by using the source's own `fetchedAt` as the validation clock.

A successful local check only certifies internally consistent JSON **from a file**. It does **not** independently authenticate the HTTP origin, verify official House PDFs, ensure full history, conclude trading returns or authorize database ingestion. A failed check must block the proposed sync.

## Local test evidence

The six E1.16 suites were executed with the socket-network guard:

```bash
PYTHONPATH=tests:. python -m pytest -p e116_network_guard -p no:cacheprovider -q \
  tests/test_congress_ingest.py \
  tests/test_congress_data_layer.py \
  tests/test_tracefour_ingest.py \
  tests/test_e115_congress_research_guard.py \
  tests/test_e116_quality.py \
  tests/test_e116_offline_preview.py
```

**97 passed in 0.15 seconds**, using local copies confirmed by Git blob hashes. GitHub Actions pytest evidence remains unconfirmed, and Vercel success is not a pytest result.

## Approval boundaries

- **Approved previously:** one read-only HTTP GET, attempted but failed. No retry is automatic.
- **Pending:** user provides saved JSON file, or explicitly approves a new, narrowly scoped read-only request.
- **Not approved:** Supabase inserts/updates, API cron activation, trading, branch merges, intentional deployments.
- Any future data-only ingestion requires a separate request and approval after offline verification.
