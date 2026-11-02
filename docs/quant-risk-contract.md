# Quant AI — Central Risk Contract v0 (isolated prototype)

**Ownership:** Quant Risk track owns `quant_risk/**`, `tests/test_quant_risk.py` and this document only. No legacy module, strategy, dashboard, CI or live order path is modified. **This branch must not be merged as a trading-enablement change.**

## Audited legacy entry points

- `quant_engine.RiskEngine.size_position(entry_price, stop_loss_price) -> int`: 1% equity at risk and 5% equity single-position notional (defaults). `validate_and_size_order` additionally handles duplicate symbols, 3% drawdown from `start_of_day_equity`, and 50% portfolio exposure. Its drawdown breaker is not latched.
- `risk_manager.RiskManager.calculate_position_size(entry_price, stop_loss_price) -> int` and `size_position` alias: 1% risk and 5% notional sizing without portfolio-aware checks.
- `live_paper_trader.LiveExecutionEngine.execute_loop`: dashboard manual orders reference `risk_engine` before initialization; both dashboard and strategy entries can call broker `submit_order`; exits call `close_position` outside the risk gate. `sync_portfolio_state` exposes equity, last equity, symbol lists, gross estimate but **not** a full atomic pending-notional snapshot or quote timestamps.
- `quant_engine.ExecutionOMS.execute_signal`: checks daily drawdown and calls `size_position` but bypasses `validate_and_size_order` portfolio/pending protections.
- `cogs.modelling.ModellingCog.position_size` calculates informational quantity from account equity.
- Existing tests in `tests/test_risk_manager.py` verify basic sizing. `tests/test_execution_gates` lacks a `.py` suffix; `tests/pytest.ini` contains `testpaths = test` and therefore should not be trusted as a complete collection baseline. The legacy cache filename test is inconsistent with the present `DataLayer.build_cache_filename` interface.

## Module boundaries and public contract

- `quant_risk.models`: frozen `RiskLimits`, `OrderIntent`, `PortfolioSnapshot`, `RiskDecision`, `KillSwitchState`; all monetary values in the portfolio equity currency, preferably `Decimal`.
- `quant_risk.policy.assess_order(intent, snapshot, limits, now, *, reserved_notional=0, reserved_symbols=()) -> RiskDecision`: pure calculation; no I/O, no broker, no network, no ML, no strategy changes. **Not** an execution authority.
- `quant_risk.gate.RiskGate(limits).preflight(intent, snapshot, now) -> RiskDecision`: thread-locked in-memory intent reservation, cumulative session-start-equity drawdown latch, operator kill, manual recovery. It **does not** submit/cancel orders or persist state.
- `RiskGate.release_reservation(intent_id) -> bool`: release confirmed terminal reservation without allowing ID replay.
- `RiskGate.trip_manual_kill(reason) -> bool`: block future preflights, clear local reservations (does not cancel broker orders).
- `RiskGate.clear_manual_kill(snapshot, now, operator_reason) -> bool`: explicit manual recovery with fresh reconciled state, **does not clear daily latch**.
- `RiskGate.advance_session(snapshot, now, operator_reason) -> bool`: explicit new-session rollover, requires changed trusted session ID, fresh non-breached snapshot and no outstanding local reservation. Does not clear independent manual kill.

### Units and mandatory inputs

`OrderIntent` fields: `intent_id` (unique across lifetime), `symbol` (uppercase equities-compatible identifier), `side` (`BUY` open-long or `SELL` open-short), `entry_price` (reference per-share price), `stop_price` (protective stop on loss side), `quote_at` (timezone-aware), and optional `requested_quantity` (positive whole shares or `None` for maximum allowed).

`PortfolioSnapshot` fields: `equity` and `session_start_equity` (positive), `gross_exposure` (absolute market value of existing holdings), `pending_exposure` (sum of absolute open-order notional), `positions` (immutable tuple of `(symbol, absolute_notional)`), `pending_symbols` (tuple), `as_of` (timezone-aware), `session_id` (trusted stable session label). Snapshot source must be **atomic, complete, verified and reconciled**; partial or untrusted data must not be passed as a valid zero-exposure snapshot.

`now` is a required, explicitly injected aware timestamp. Default max age: 90 seconds for both price and portfolio snapshot. No implicit wall clock or provider access. Decimal conversion accepts finite numbers, rejects strings, booleans and non-finite values. All quantities are nonnegative whole shares.

### Defaults (match existing risk concepts, not guaranteed fill protection)

| Limit | Value | Handling |
| --- | ---: | --- |
| Per-trade stop distance × shares | <= 1% current equity | Floor to whole shares |
| Per-symbol gross order notional | <= 5% current equity | Floor to whole shares |
| Combined gross held + broker pending + locally reserved + candidate | <= 50% current equity | Floor to whole shares |
| Cumulative drawdown relative to session start equity | < 3% | Latch deny for session until explicit next session |
| Duplicate symbol / broker pending order / locally reserved symbol | None allowed | Reject |
| Duplicate intent ID (including after release) | None allowed | Reject |
| Invalid stop side, invalid/stale price, invalid/stale snapshot, requested quantity over cap | None allowed | Default-deny |

These rules cover **new equity entries only**. They do not cover exit orders, cash vs short margin, contract multipliers, FX currency conversion, borrow availability, account buying power, exchange trading status, price slippage, stop execution guarantees, or corporate-action adjusted sizing. No order should be sent based only on this v0 output.

## Acceptance criteria and deterministic tests

1. Importing `quant_risk` never creates broker clients or reads credentials.
2. Sizing preserves 1%/5% concepts, applies 50% aggregate gross exposure including pending and reserved amounts, and rejects explicit over-limit requested quantities.
3. No approval for stale or future quotes/snapshots, invalid numeric values, mismatched positions, duplicate symbols, or non-loss-side stops.
4. Concurrent local approvals for the same symbol are serialized and cannot both succeed.
5. 3% daily equity loss immediately latches and cannot clear upon same-session equity recovery.
6. Manual kill remains independent of daily kill, with explicit recovery.
7. `RiskDecision` is immutable and every rejection has quantity zero.
8. Tests require only the Python standard library and deterministic injected timestamps.

Run from repo root: `python -m unittest tests.test_quant_risk -v` (or `python -m pytest tests/test_quant_risk.py -q` if pytest is installed).

## Integration proposal for Master Architecture (not implemented)

1. **Agree v0 fields and ownership.** Master Architecture/OMS owns converting broker data into an atomic `PortfolioSnapshot`, trusted exchange `session_id`, order IDs, state storage and all broker interactions. Risk track owns only `quant_risk/**`.
2. **Read-only shadow evaluation first.** Compare `assess_order` on identical snapshots against `RiskEngine.size_position` and `RiskManager.calculate_position_size` for plain long entries; document intentional differences (invalid stop side, exposure limits, stale-data/duplicate rejection). No strategy signals changed.
3. **Durability gate before authorization.** OMS must use a shared transactional database for atomic intent-ID claims and symbol reservations, persistent kill state, broker order reconciliation (including cancel/fill/reject), a trusted clock/session calendar, authenticated manual recovery, and audit events. In-process reservations alone are insufficient for production.
4. **Single order authority.** In a separate PR, refactor `quant_engine.ExecutionOMS` and `live_paper_trader` order entry, dashboard commands, Discord entry points, and exits through an OMS adapter with broker preflight and post-fill reconciliation; avoid changing signal algorithms. Refuse to place an order when risk state is unavailable.
5. **Deprecate incrementally.** Keep both legacy classes as compatibility wrappers only after an approved interface migration with parity tests. Never silently replace a legacy method because stricter stop-side semantics and preflight rejections are intentional behavior changes.
6. **Separate security hygiene.** The public repository tree includes a tracked `.env` file. Without inspecting or exposing its values, treat this as a potential secret leak for a separately approved credential rotation and history-remediation track.

**Operational status:** research-only and offline tests; no connected broker, no live/paper order submission, no deployment, no merge. Independent review is mandatory.
