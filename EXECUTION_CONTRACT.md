# Execution Contract v1

This module is the single execution and accounting engine for downstream research adapters. Strategies provide orders; this package owns eligibility, fills, costs, margin checks, FIFO position accounting, trade ledger, marks, rejection records, and provenance.

## API

\`ExecutionEngine.run(bars, orders, cost_profile=..., config=...)\` returns fills, round trips, rejections, an equity mark, open positions, invalidated trades, and provenance. \`ExecutionOrder\` carries symbol, market, contract, signal time, information availability time, order time, side, quantity or target position, order type, TIF, strategy ID, and experiment ID.

Input bars must carry \`bar_start\`, \`bar_end\`, \`available_at\`, timezone, session, OHLCV, contract, data/backtest eligibility, separate buy/sell eligibility, rejection reason, and explicit session open/close. The engine rejects unknown label/session semantics and unknown sessions. A signal cannot execute before its information or order time. Same-timestamp availability and bar open are accepted only when \`same_timestamp_open_assumption=True\`, and the fill records that assumption.

Only MARKET and LIMIT orders and NEXT_BAR and DAY TIF are implemented. NEXT_BAR orders inspect eligible bars after the order time and cannot remain pending across unrelated market dates; unsupported combinations are not implied by the API. No forced close is applied at end of data. FIFO matching is per symbol and contract. Exclusion overlap produces a separate invalidated ledger.

## Market timing evidence

- Taiwan equity: producer schema names the timestamp \`bar_start\`; the producer's 2026-10-23 capability smoke recommends \`start\`. The 5-minute close auction can extend to 13:33, and same-auction close fills are prohibited by the producer contract. Use next-session execution for information available after the close auction. Source evidence does not support using the 13:30 print as an executable same-close price.
- TXF 1-minute: producer session and aggregation validation do not distinguish bar-start from bar-end labels. Status: UNKNOWN. Formal backtests are blocked pending producer evidence.
- IBKR TSM: formal manifest demonstrates RTH coverage, but does not establish that the dataset contains only RTH or define an execution session mask. Status: UNKNOWN. Formal backtests are blocked pending a TSM-specific session audit.

## Costs

Cost profiles are immutable dataclasses with canonical SHA-256. Every profile includes source evidence, basis (OFFICIAL, BROKER_ACTUAL, or ASSUMED), a version, and approval status. Values marked ASSUMED or profiles marked DRAFT support research-only runs and cannot produce FORMAL_ELIGIBLE. No broker rate is embedded in the engine. Equity costs include proportional/minimum commission and sell tax; futures costs include per-contract-per-side fee, transaction tax, multiplier, tick, and adverse slippage. Futures opening does not debit full notional; margin is tracked separately.

## Phase 3 mapping

Map Phase 3 bar fields directly: timestamp to \`bar_start\` only when source semantics confirm START; retain explicit \`bar_end\`, \`available_at\`, timezone and session; map its side eligibility fields one-to-one. Map request orders to \`ExecutionOrder\`. Return engine rows as fills, round_trips, rejections, equity_curve, open_position, invalidated_round_trips, and provenance. The adapter must not calculate fills or PnL.

## Benchmark status

B01-B12 are NOT RUN in this repository. The independent Phase 3 Oracle remains authoritative; no oracle answers were edited. A downstream adapter must compare these cases before benchmark PASS can be claimed. Unknown TXF label and IBKR TSM session coverage remain market-specific blocks.

## Limitations

- Futures margin is represented by a required initial margin per contract supplied by the caller; no exchange margin table is embedded.
- Data quality/exclusion segmentation for indicator computation remains the signal producer's responsibility. The engine prevents fills inside configured exclusions and invalidates trades overlapping them.
- Formal status requires approved costs and complete pinned provenance. Research runs remain explicitly research-only.
