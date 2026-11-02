# E1.21 — Pelosi/Tracefour single-request connection diagnostic

**Status: NO-GO for ingestion, automated provider fetching, scheduling, Supabase writes, broker orders, merges or deployment.** Implementation is confined to `feature/e116-congress-readiness`.

## Actual one-request outcome (2026-11-02)

The user explicitly approved **one** read-only HTTP GET to `https://tracefour.com/v1/congress/nancy-pelosi`.

- **Attempted:** one retrieval using this conversation's web retrieval capability.
- **Observed:** `Failed to fetch https://tracefour.com/v1/congress/nancy-pelosi: Cache miss`.
- **Not observed:** Tracefour's HTTP status, headers, current JSON body, current `meta.fetchedAt`, or evidence of an end-to-end TLS/HTTP connection.
- A retrieval-system `Cache miss` **is not an HTTP 404 returned by Tracefour**.
- **No second live API request was performed**. This outcome does not establish a provider outage, an API-key requirement, or what a separately hosted backend would experience.

## Separately verified older user-provided JSON snapshot

Uploaded file `tracefour_pelosi.json`: SHA-256 `4165d091ccf389f9eb4a5012adcc88ea9241b56bc086b6fed03be3249383c5f3`, 147 reported transactions, member `nancy-pelosi`. It is not a live E1.21 response.

At the explicitly recorded validation clock of `2026-11-02T10:06:40Z`, its `meta.fetchedAt` was `2026-11-02T02:53:54.620Z`: age **7h12m45.38s**, exceeding the project's six-hour cache freshness gate. **21 original HTTPS filing links are missing**. Thus this snapshot also fails the strict ingestion validation contract independently of the transport issue. No official House filing PDF was independently verified. Provider cache freshness does not prove historical completeness or real-time trade observability.

## New read-only diagnostic implementation

`congress_connection_diagnostic.py` implements:

1. `diagnose_saved_json(path, now=...)`: default **offline-only** CLI that checks saved UTF-8 JSON, member identity, `meta.count` (member count, not trade count), source age, filing-link presence and host, malformed trades, 500-row boundary, and the existing strict contract. It reports `http_status=null`, `live_response_verified=false`, `outbound_http_requests=0`, `supabase_writes=0` and `orders_submitted=0`. There is no CLI option to fetch external data.
2. `probe_once(session, now=...)`: injectable transport function for **a separately approved future call only**. Invokes `session.get` once in the source code with fixed Tracefour URL, JSON accept header, connect/read timeouts, `allow_redirects=False`, and streamed response up to 2 MB; reports DNS/connection exception **without inventing an HTTP code**, non-2xx/3xx, invalid media types and source-quality blockers separately. It doesn't construct a session or set retries; callers must provide a no-retry adapter to meet a strict one-network-attempt approval. E1.21 ran this only with **fake injected sessions**.

Both modes preserve `approved_for_ingestion=false`, `approved_for_trading=false`, and do not import or call a Supabase or broker SDK. No live `probe_once` call was performed.

## Executed local tests

With the exact GitHub `congress_quality.py` blob `e17da100eafbf43a98311b233c5af91eb9e82100`, the new module and tests:

```bash
E121_SNAPSHOT_PATH=/mnt/data/tracefour_pelosi.json \
PYTHONPATH=tests:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p e116_network_guard -p no:cacheprovider -q \
  --junitxml=/mnt/data/e121_connection_junit.xml \
  tests/test_e121_connection_diagnostic.py
```

**22 passed, 0 failed, 0 skipped** in approximately 0.04 seconds; socket-blocking plugin enabled. Includes the exact 147-row user snapshot SHA and 21 broken links, stale-time bounds, fake HTTP 200/302/503, no HTTP response, HTML, invalid JSON and oversized body, one session.get invocation, and confirmation that no live CLI flag exists.

The private source JSON is **not committed**; its optional test skips in public CI unless a local `E121_SNAPSHOT_PATH` is provided. A future GitHub Actions run is not independently verified by this local result. The previous E1.16–E1.20 tests were not rerun locally in this milestone.

## Outstanding blockers and next authorization

- Live Tracefour connectivity remains **unverified** from this environment; this attempt did not return Tracefour's HTTP response.
- The older saved snapshot fails freshness and filing-provenance requirements; E1.17–E1.20 quarantine/evidence adjudication remain intact.
- Any new live provider request, database import, recurring schedule, broker order, merge or deployment needs its own explicit approval. No further API request is authorized by this milestone.
