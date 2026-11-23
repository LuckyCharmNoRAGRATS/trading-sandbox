# Quant execution simulator — isolated v1 contract

Scope: **offline simulation only**. No Alpaca, Supabase, broker, dashboard, Discord, credentials, network, strategy, deployment, or live-trading imports. This is **not** the E1 safety gate and cannot authorize broker orders.

## Ownership / boundaries

- `models.py`: immutable `OrderIntent`, `Quote`, `CostModel`; numeric and timestamp validation.
- `simulator.py`: deterministic order-state engine, quote/depth matching, cash/position/commission ledger, hash-linked event history, atomic manual journal snapshot/recovery.
- `__init__.py`: stable public import surface.
- `tests/test_quant_execution_simulator.py`: isolated regression tests.
- Existing `live_paper_trader.py`, `quant_engine.py`, `order_lifecycle.py` (on `fix/e1-safe-execution`), `dashboard_bridge.py`, risk controls, and strategies are **not changed**.

## Public API

```python
from datetime import datetime, timezone
from quant_execution import CostModel, ExecutionSimulator, OrderIntent, Quote

sim = ExecutionSimulator("10000", CostModel(slippage_bps="2", commission_per_share="0.005", latency_ms=50))
intent = OrderIntent(client_order_id="sim-1", idempotency_key="scenario1-signal7",
                     symbol="SPY", side="buy", quantity="5", order_type="limit", limit_price="102")
sim.submit(intent, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
sim.on_quote(Quote(datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
                   bid="100", ask="101", bid_size="10", ask_size="10"))
print(sim.reconcile())
```

- `submit(intent, at=aware_utc_time)`: `ACCEPTED`; identical `(idempotency_key, intent)` is a no-op. Conflicting keys/order IDs fail closed. Caller must use a stable key **per logical action**, not per retry. No risk approval is inferred.
- `cancel(id, at=..., reason=...)`: `ACCEPTED|PARTIALLY_FILLED -> CANCELED` retaining filled quantity.
- `reject(id, at=..., reason=...)`: `ACCEPTED -> REJECTED` only.
- `on_quote(Quote)`: fills orders in arrival order from shared displayed quote sizes; returns count of fill events. Quotes must have strictly increasing timestamps. Market orders use ask for buys / bid for sells. Limit orders fill only if the **slippage-adjusted** execution price satisfies the limit. Latency gate delays eligibility.
- `events()`: detached, numbered, hash-linked event history; includes quotes and incremental fills.
- `ledger()`, `reconcile()`: string-serialized Decimal cash/positions/fees and purely **simulation-to-simulation** replay comparison. Negative simulated balances and short positions are permitted; no margin, short locate, exchange sessions, halts, tick size, buying power, or capital gate is modeled.
- `save_journal(path)`, `load_journal(path)`: atomically save/reload a manual single-process JSON snapshot to an existing directory; hash mismatch, missing file and corrupt journals fail closed. **Persistence is not automatic or transactional with commands**. Unsaved events cannot be recovered. Do not describe this as exactly-once or durable production OMS behavior. Not multiprocess-safe and not tamperproof against rehashing/suffix deletion.

## Prices, fees and determinism

Bid/ask spread is **already embedded** in side-of-market fill prices; apply nonnegative extra slippage in **basis points** on top, then nonnegative `commission_per_share * filled_shares`. No hidden 8 bps are added. The existing `QuantConfig.spread_slippage_bps=0.0008` is a vectorized *return fraction*, **not** a per-order bps setting. Convert explicitly only after an architecture decision; do not reuse it implicitly. `latency_ms` is fixed from acceptance time and does not model stochastic queues. `Quote.ask_size` and `Quote.bid_size` represent synthetic executable depth at the single displayed price and are shared by all orders. Explicit simulated timestamps and Decimal arithmetic make repeats deterministic.

## Safe future adapter integration (requires Master Architecture approval)

`Strategy Signal -> Risk Gateway -> ApprovedOrderIntent mapper -> ExecutionSimulator.submit(...) -> simulator events/ledger -> Research report`

The mapper should be a **separate** read-only/offline integration module, not part of the simulator. Define a versioned `ApprovedOrderIntent` including approval decision/version, immutable snapshot identity, idempotency key, UTC time, symbol, side, quantity and order type; reject absent/expired approval. This simulator does not accept a trading client, has no `submit_order` method, and must never be wired to the live worker automatically. Broker/E1 order state (`order_lifecycle.py`) uses **cumulative** broker fills; simulator `FILL` events contain **incremental** quantities. A future adapter must explicitly translate this difference and not infer broker authorization from simulated states.

## Acceptance and exclusions

Offline tests cover duplicate submits, key collisions, terminal transition invalidity, cancellation after partial fills, quote-depth sharing, order type/limit checks, latency, cost and ledger arithmetic, deterministic replay, restart from a saved journal, corrupt/invalid replay and lack of broker imports. Full repository integration tests, concurrency, power-loss recovery and broker safety gates are **not** established by these tests. The feature branch is intentionally independent from `fix/e1-safe-execution` and `test/phase1-pytest-baseline-regression`. Keep it unmerged pending Master Architecture review.
