# M3-A.1 IBKR TSM Execution Gate Closure

**Decision: SCOPED_GO for bounded, read-only research diagnostics on the pinned snapshot. NO TSM ExecutionEngine run or formal/performance backtest is authorized.** The source timestamp label and actual historical publication latency remain unknown. This report reuses existing FINAL GATE evidence only; no full scan, download, or FINAL GATE rerun was performed.

## Fixed scope and evidence

| Item | Value |
|---|---|
| Core PR / branch | [PR #3](https://github.com/hh4832/market-research-core/pull/3), `feature/m3a-ibkr-tsm-readiness` |
| Core base | `bf01fa92bc95f9fc8b04038875c813f6cd30ad99` |
| Dataset | `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL` |
| Producer SHA | `06359faf7ef4b8ff702a7ab1a6d07521d3b68f28` |
| Existing data evidence | 2,193,372 rows; existing FINAL GATE verdict GO (not execution approval) |
| Request evidence | 1 minute, TRADES, `useRTH=False`, `formatDate=2`, `keepUpToDate=False` |
| Calendar implementation | Core dependency pinned to `pandas-market-calendars==5.5.0`; NYSE schedule, America/New_York |
| Probe | No real-data execution probe; no archive created |

## Timestamp label verdict

**UNKNOWN: cannot determine whether the IBKR one-minute timestamp labels bar_start or bar_end.**

The pinned cleaning producer source SHA is not the downloader SHA and does not pin an acquisition receipt. The downloader source reviewed converts IBKR's `date` field to a UTC epoch timestamp without declaring interval start/end semantics. IBKR API documentation defines `formatDate=2` as epoch representation and `useRTH=0` as allowing outside-RTH data; neither specifies minute-bar label semantics. UTC conversion is not evidence for bar_start.

This finding applies to the pinned one-minute `TRADES` TSM data path only. It must not be generalized to other IBKR bar sizes, data types, or producers. No bounded underlying-trade correlation was available, so no label was inferred. Potential producer/API-version exceptions remain unassessed.

**Minimum evidence to resolve:** the exact downloader revision and request receipt for this snapshot, plus a bounded sample correlating a returned minute bar to the underlying trade interval or an explicit IBKR definition. Do not scan the full dataset.

## Availability policy

- **Observed release time:** UNKNOWN. The historical snapshot does not record when a finalized bar was published or became available to the strategy.
- **Conservative simulation policy:** `BAR_END_STRICT_LATER` models availability at `bar_end`, forbids execution at a candidate bar whose `bar_start == available_at`, and requires a later eligible bar. This is a conservative as-of assumption, not measured IBKR latency.
- **Current TSM adapter:** uses timestamp label `UNKNOWN` and availability `UNKNOWN`; leaves `bar_start`, `bar_end`, and `available_at` missing and fails closed.

Using the conservative policy for a real TSM execution requires research-owner acceptance. It cannot be represented as observed publication timing.

## Session mask

The new mask uses a pinned NYSE calendar package and IANA `America/New_York` conversion for DST, holidays, and scheduled early closes. It labels completed one-minute intervals as `RTH`, `PRE_MARKET`, `AFTER_HOURS`, or `OUTSIDE_SESSION`; intervals crossing session boundaries are outside-session. It never fills missing bars. Request evidence establishes RTH plus available extended-hours coverage, not 24-hour coverage.

The mask is validated synthetically for RTH open, pre-market, early close, after-hours, outside-session, holiday, DST, and equivalent START/END mappings. Because the real TSM label is unresolved, this mask is not applied to the real snapshot. Classification and side eligibility are separate: current buy and sell eligibility remain false for every TSM row. If enabled after timestamp evidence, the initial research execution scope is RTH only; ETH is not enabled.

## Cost model compatibility and approval

Core CostProfile keeps the existing API and adds representation for per-share commission, minimum commission, bps slippage, half-spread assumption, sell-side regulatory fee, and volume participation cap. Populated optional components require component-specific basis, evidence, and approval status. These are carried into execution provenance with the cost profile ID and SHA.

The volume cap rejects an order exceeding the configured fraction of the candidate bar's volume; it does not create a partial fill. Synthetic tests verify the component calculations and ledger flow. No rate is supplied or approved by this work. The Agent profile remains DRAFT/ASSUMED; no actual account commission evidence or research-owner cost approval was obtained. Values must not be inferred from a plausible market convention.

## Execution eligibility matrix

| Path | Result | Rule |
|---|---|---|
| Load pinned TSM snapshot for a bounded read-only diagnostic | Allowed, RESEARCH_ONLY | Keep source UTC timestamp unchanged |
| Derive PIT signals from source bars | Not approved | Actual release time and source bar completion boundary are unknown |
| Buy execution | Rejected | `buy_execution_eligible=False`; `TIMESTAMP_LABEL_OR_SESSION_UNKNOWN` |
| Sell execution | Rejected | `sell_execution_eligible=False`; `TIMESTAMP_LABEL_OR_SESSION_UNKNOWN` |
| RTH/ETH fills on this snapshot | Not approved | Session label cannot be mapped until timestamp label is known |
| Formal performance backtest | Not allowed | Timestamp, observed availability, execution approval, and approved costs unresolved |

## Regression and CI

Targeted synthetic tests cover fail-closed unknown timing, pinned NYSE mask behavior at early close/DST/holiday/session boundaries, conservative available-at behavior, cost calculations and ledger accounting, volume-cap rejection, and component-level cost provenance. Existing Oracle B01–B12/G16/G17/G14b compatibility was not modified or re-run as part of this bounded market gate change.

Python 3.11 GitHub Actions is the authoritative test environment. The final head SHA and its CI result are recorded in PR #3. No local environment was created or modified.

## Remaining blockers and owners

1. **Timestamp label:** Data/producer owner supplies pinned downloader revision/receipt and bounded trade-interval correlation or authoritative semantics.
2. **Availability and session approval:** Data owner supplies actual publication evidence if available; research owner explicitly accepts or rejects `BAR_END_STRICT_LATER`. Once the label is resolved, generate the row-level NYSE session mask; RTH only by default.
3. **Cost and execution approval:** Account owner supplies actual pricing evidence or research lead approves explicitly assumed components; research lead approves volume participation and side-specific execution policy.

Until these items are resolved, only bounded, read-only RESEARCH_ONLY diagnostics are in scope. There was no real-data execution probe, so no Drive archive or performance result exists.
