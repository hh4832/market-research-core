# Execution Contract v1

This module is the single execution and accounting engine for downstream research adapters. Strategies provide orders; this package owns eligibility, fills, costs, margin checks, FIFO position accounting, trade ledger, marks, rejection records, and provenance.

## API

\`ExecutionEngine.run(bars, orders, cost_profile=..., config=...)\` returns fills, round trips, rejections, an equity mark, open positions, invalidated trades, ordered order events, and provenance. \`ExecutionOrder\` carries symbol, market, contract, signal time, information availability time, order time, side, quantity or target position, order type, TIF, strategy ID, and experiment ID.

Input bars must carry \`bar_start\`, \`bar_end\`, \`available_at\`, timezone, session, OHLCV, contract, data/backtest eligibility, separate buy/sell eligibility, rejection reason, and explicit session open/close. The engine rejects unknown label/session semantics and unknown sessions. A signal cannot execute before its information or order time. Same-timestamp availability and bar open are accepted only when \`same_timestamp_open_assumption=True\`, and the fill records that assumption.

Only MARKET and LIMIT orders and NEXT_BAR and DAY TIF are implemented. NEXT_BAR orders inspect eligible bars after the order time and cannot remain pending across unrelated market dates; unsupported combinations are not implied by the API. No forced close is applied at end of data. FIFO matching is per symbol and contract. Exclusion overlap produces a separate invalidated ledger. A run containing any invalidated trade is downgraded to RESEARCH_ONLY so an adapter cannot use that curve as formal performance; excluded-segment aggregation remains an adapter/harness responsibility.

## Market timing evidence

- Taiwan equity: producer schema names the timestamp \`bar_start\`; the producer's 2026-10-23 capability smoke recommends \`start\`. The 5-minute close auction can extend to 13:33, and same-auction close fills are prohibited by the producer contract. Use next-session execution for information available after the close auction. Source evidence does not support using the 13:30 print as an executable same-close price.
- TXF 1-minute: producer session and aggregation validation do not distinguish bar-start from bar-end labels. Status: UNKNOWN. Formal backtests are blocked pending producer evidence.
- IBKR TSM: formal manifest demonstrates RTH coverage, but does not establish that the dataset contains only RTH or define an execution session mask. Status: UNKNOWN. Formal backtests are blocked pending a TSM-specific session audit.

## Costs

Cost profiles are immutable dataclasses with canonical SHA-256. Every profile includes source evidence, basis (OFFICIAL, BROKER_ACTUAL, or ASSUMED), a version, and approval status. Values marked ASSUMED or profiles marked DRAFT support research-only runs and cannot produce FORMAL_ELIGIBLE. No broker rate is embedded in the engine. Equity costs include proportional/minimum commission and sell tax; futures costs include per-contract-per-side fee, transaction tax, multiplier, tick, and adverse slippage. The request's cost multiplier scales fees, taxes and slippage and is recorded in provenance. Futures opening does not debit full notional; margin is tracked separately.

## Phase 3 mapping

Map Phase 3 bar fields directly: timestamp to \`bar_start\` only when source semantics confirm START; retain explicit \`bar_end\`, \`available_at\`, timezone and session; map its side eligibility fields one-to-one. Map request orders to \`ExecutionOrder\`. Return engine rows as fills, round_trips, rejections, equity_curve, open_position, invalidated_round_trips, and provenance. The adapter must not calculate fills or PnL. Pass `spec_sha256`, parameters, cost multiplier, seed and request windows through `ExecutionConfig`; the engine emits the Phase 3 convention identifier `oracle-execution-convention-v1`.

## Phase 2.1 acceptance remediation

The M1.2 independent cross-check on Core `298956f78522a9f2d13efbd38645178b1b477691` passed B01–B12 (12/12), Decimal tolerance 0, G16 chronology (6/6), and the Runner→Core→Registry integration path. It identified the remaining G17 and G14b probes. This section records their Core closure; independent rerun on the new Core SHA is pending.

- **G16 chronological execution:** one event loop advances in `bar_start` order. At each bar, eligible live orders are evaluated by `order_time` and order ID. Cash, margin, FIFO lots and actual filled position change at the fill event.
- **G12 cost arithmetic:** `calculate_costs` uses Decimal inputs and returns Decimal fee/tax values. Rounding supports `HALF_UP`, `UP`, `DOWN` (currency cents), `NONE` (unrounded), and `FLOOR_INTEGER` (floor to whole currency units). Account PnL is not rounded again to cents.
- **G14b expiry boundary:** DAY validity is the half-open interval `event_ts < expiry`, where expiry is the first `session_close > order_time`. A candidate at the exact expiry time cannot fill, including a next-session bar opening at that timestamp. `ORDER_EXPIRED` keeps `bar_ts=null` for compatibility and records the exact `expiry_ts`. A fill strictly before expiry remains valid. NEXT_BAR retains its one-candidate behavior.
- **G15 rejection audit:** every extant candidate bar skipped for session, data, volume, side eligibility, exclusion, or limit reasons is recorded with `order_id`, `bar_ts`, and a specific `reason`, including when a later bar fills the order.

## Benchmark status

The independent Phase 3 Oracle remains authoritative; no Oracle answers or fixtures were edited. On the M1.2 baseline SHA above, B01–B12 were **12/12 PASS**, exact Decimal tolerance 0, and G16 was **6/6 PASS**. G17 and G14b are now covered by Core regressions; the independent Oracle rerun against the new Core SHA is pending and is required before claiming final acceptance.

| Case | M1.2 result on 298956f | New Core verification |
| --- | --- | --- |
| B01–B12 | PASS (12/12) | Independent rerun pending |
| G16 chronology | PASS (6/6) | Regression retained |
| G17 pending target intent | FAIL | Core regression added; Oracle rerun pending |
| G14b expiry boundary | FAIL | Core regression added; Oracle rerun pending |

Unknown TXF label and IBKR TSM session coverage remain market-specific blocks. Synthetic Oracle acceptance does not authorize formal market backtests.

## Limitations

- Futures margin is represented by a required initial margin per contract supplied by the caller; no exchange margin table is embedded.
- Data quality/exclusion segmentation for indicator computation remains the signal producer's responsibility. The engine prevents fills inside configured exclusions and invalidates trades overlapping them.
- Formal status requires approved costs and complete pinned provenance. Research runs remain explicitly research-only.


## G17 target-position pending orders

A target order's quantity is fixed once, when it becomes active at `order_time`. The target delta is:

`desired target - (actual filled position + signed remaining quantity of live target-position orders for the same strategy/symbol/contract)`.

This matches the pinned Oracle v1 rule. A later target does **not** rewrite prior fills and does not cancel an older pending target order; it creates a balancing target order against that live intent. For example, with a pending BUY +1000, a new target 0 activates a SELL 1000. The older BUY remains eligible and may still fill later, matching the Oracle's expected short-then-flat chronology. This is the approved replacement semantics for this Core contract.

Target-order activation, rebalance, fill, expiry, and rejection are exposed in `ExecutionResult.order_events` with timestamps and reason codes. When a later target balances an earlier pending target, the event links both order IDs and records that the earlier order remains live for Oracle v1 compatibility. Same-time activations sort by explicit order ID, then canonical order SHA. Explicit-quantity orders are independent: their pending quantity is excluded from target projection and a target signal does not cancel them; their actual fills do update that strategy's filled position used by subsequently activated targets.

Execution is full-quantity only. Partial fills are unsupported; an order either fills its full requested/derived quantity at one eligible bar or remains pending/rejects according to TIF and execution gates. The engine does not represent partially filled open orders.



## M3-A IBKR TSM market gate

The pinned TSM snapshot may be loaded for bounded research diagnostics, but its
`GO` data-quality verdict is not execution approval. The producer request uses
`useRTH=False` and therefore combines RTH with available extended-hours data.
The one-minute timestamp label, per-bar session, and `available_at` remain
`UNKNOWN`. The unified adapter keeps `bar_start`, `bar_end`, and
`available_at` unavailable, sets both side eligibility flags false, and rejects
with `TIMESTAMP_LABEL_OR_SESSION_UNKNOWN`. Provenance is `RESEARCH_ONLY` while
execution eligibility is false.

The Agent's IBKR equity profile remains DRAFT and ASSUMED. Core's percentage and
tick-based cost fields cannot faithfully represent its per-share commission and
basis-point slippage, and spread/volume participation are unresolved. Do not run
a TSM ExecutionEngine probe or present formal performance until the timestamp,
availability, session mask, cost mapping, and approvals are evidenced. See
[the M3-A readiness report](docs/M3A_IBKR_TSM_READINESS.md) and
[machine-readable diagnostic](docs/diagnostics/m3a_ibkr_tsm_readiness.json).


## M3-A.1 IBKR TSM execution gate

### Timestamp and availability

For the pinned TSM snapshot, the source timestamp is **UNKNOWN** as either a minute-bar start or end label. The pinned cleaning producer SHA is `06359faf7ef4b8ff702a7ab1a6d07521d3b68f28`; it does not pin the downloader revision or a request receipt that defines the label. The downloader code available for inspection converts IBKR's `date` field to UTC epoch timestamps, but that conversion does not establish interval semantics. IBKR's documented `formatDate=2` and `useRTH=0` parameters describe timestamp representation and RTH filtering only. Until a receipt plus bounded trade-interval correlation or an explicit provider statement resolves the label, Core must leave `bar_start` and `bar_end` unknown and reject TSM execution.

Actual historical publication latency is a separate fact and is **UNKNOWN** for this snapshot. `BAR_END_STRICT_LATER` is a conservative simulation policy: it models information as available at the completed bar's end, forbids same-timestamp bar-open execution, and requires execution on a later eligible bar. It is not an observation or estimate of IBKR publication delay. `OBSERVED` is permitted only when actual availability evidence is supplied. The TSM data client currently selects `UNKNOWN` for both timestamp label and availability and therefore remains research-only.

### Session classification

`apply_tsm_session_mask` uses the pinned `pandas-market-calendars==5.5.0` NYSE schedule and America/New_York timezone conversion. It distinguishes `RTH`, `PRE_MARKET`, `AFTER_HOURS`, and `OUTSIDE_SESSION`, including DST, holidays, and scheduled early closes. A bar crossing a session boundary is `OUTSIDE_SESSION`. Session labels do not grant execution eligibility. Until the timestamp label is confirmed, the TSM client keeps every row's session unknown and both buy/sell eligibility false. When enabled after evidence review, the default research execution scope is RTH only; ETH remains ineligible unless separately approved. Missing bars are never synthesized.

### Cost model mapping

The backward-compatible CostProfile now represents per-share commission with a per-order minimum, equity slippage and half-spread in basis points, sell-side regulatory fee rate, and a volume participation cap. Component-level basis, evidence, and approval status are required for each populated optional component and flow into run provenance alongside the profile SHA. The volume cap rejects an order that exceeds the candidate bar's permitted volume; partial fills are unsupported. No IBKR rate is supplied or approved by this change. Empty or assumed values remain non-formal and do not satisfy the formal execution gate.

### TSM gate outcome

For the fixed snapshot `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`, use only bounded read-only diagnostics on the original timestamp. Do not derive bar boundaries, availability, session, or fills. Synthetic session and cost regressions do not constitute a real-data ExecutionEngine probe. Formal TSM execution remains blocked pending timestamp/receipt evidence, owner acceptance of an as-of policy, and approved cost and eligibility inputs.

## M3-A.4 conservative research and configurable costs

See [the exact released API and field contract](docs/M3A4_TEMPORAL_COST_CONTRACT.md).
`CONSERVATIVE_LABEL_AGNOSTIC` preserves UNKNOWN source labels and uses explicit
START/END uncertainty envelopes. It is always RESEARCH_ONLY. The unchanged
MarketDataClient TSM default remains fail-closed; source-label confirmation and
formal market approval are separate from this opt-in research model.
