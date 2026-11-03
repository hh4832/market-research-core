# Unified Data Access Contract v0.2

## Invariants

1. Every adapter pins snapshot identity, producer commit, verdict and required manifests before reading bars.
2. Every referenced file is read-only. Required SHA or manifest ambiguity fails closed.
3. Missing bars are never forward-filled, interpolated, converted to zero-volume bars or used as executions.
4. Futures rolls are never inferred. NQ/ES require explicit quarterly contracts; TXF preserves `actual_contract_month`.
5. A unified API never removes native timezone, session, contract or exclusion semantics.

## Four independent states

Each row keeps `data_exists`, `research_eligible`, `backtest_eligible`, and
`execution_eligible` separate. `UNKNOWN` is never treated as true.

## IBKR

Pins FINAL GATE FULL run `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`
and producer `06359faf7ef4b8ff702a7ab1a6d07521d3b68f28`. `SCOPED_GO`
exclusions remain mandatory. NQ/ES cannot be loaded without an explicit contract.

## TXF

Pins formal run `20261014_213932_404db609cf13` and producer
`404db609cf136aae050e09f1e7712a7894f6ab6d`. The canonical sample-matrix hash
and raw CSV byte SHA are verified separately. Only 58 predeclared primary files
and 206,938 rows are approved; weekly raw caches are not promoted.

## Taiwan equity

Pins FINAL GATE `final_gate_20261031_154458_20eba73`, `fubon_5m` seq 68,
quality-mask SHA `9ec79fe02412c25dae56572a334c167c168e374a438dd71e9f4dc0e138ac6863`,
B3a `corporate_actions_v2_20261031_200705_75aed1d`, B4a
`trading_rules_20261031_222459_c5c5a4b`, and B5a
`universe_pit_20261031_222454_c5c5a4b`.

Only section-26 common-stock scopes are supported. ETF/ETN/REIT, special
sessions, quarantine 8101, `UNKNOWN` limit/tradability, missing PIT evidence and
the 13:30 closing auction fail closed. Same-day quality is used only for post-hoc
invalidation. Cash dividends are modelled at effective session because actual
payment dates are unavailable; strategies depending on reinvestment timing remain
out of scope.

## Backtest protocol

The Phase-2 SMA probe uses only information available at signal time and executes
on a later `execution_eligible` real bar. It records source snapshot, producer SHA,
files/partitions, exclusions, parameters, costs, code version, trade log, equity
curve, rejections, return and drawdown. It is an integration test and cannot be
presented as formal tradable performance.

## Out of scope

- Historical FINAL GATE reruns or data repair
- Autonomous strategy generation
- Live trading, broker authentication or orders
- Unapproved corporate-action, delisting or roll assumptions
