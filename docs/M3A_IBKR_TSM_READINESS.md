# M3-A IBKR TSM Market Readiness

**Decision: NO-GO for formal TSM backtests.** The pinned snapshot is usable for bounded, read-only research and data diagnostics. Execution remains fail-closed because the producer's one-minute timestamp label and per-bar session/availability semantics are unconfirmed. The cost assumptions are DRAFT and cannot be represented losslessly by Core's current cost model.

## Fixed evidence

| Item | Evidence |
|---|---|
| Core base | `main` at `bf01fa92bc95f9fc8b04038875c813f6cd30ad99` |
| Dataset snapshot | `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL` |
| Snapshot location | [Google Drive snapshot](https://drive.google.com/drive/folders/1KZYX8ODg5o8gss5LgBxjJEzaFvObuh_d) |
| Producer SHA pinned by Core | `06359faf7ef4b8ff702a7ab1a6d07521d3b68f28` |
| TSM verdict / rows | `GO` / 2,193,372 rows; pre-existing FINAL GATE summary reports 0 errors, 0 unresolved minutes, 0 excluded candidates |
| Main CI baseline | [Run 41712424841](https://github.com/hh4832/market-research-core/actions/runs/41712424841), completed successfully on the base SHA |
| Local execution environment | No Core checkout is present in the workspace. Python is 3.12.14; pandas 2.2.3; pyarrow is unavailable. No environment was created or modified. Feature-branch CI is the test evidence for this PR. |

The FINAL GATE evidence is reused as-is. This work did not rerun the gate, alter source data, or read the full Parquet snapshot.

## Timestamp, timezone, and session findings

### Confirmed

- The IBKR request source uses `barSizeSetting="1 min"`, `whatToShow="TRADES"`, `useRTH=False`, `formatDate=2`, and `keepUpToDate=False`. The downloader documentation describes TSM/SOXX `useRTH=False` as including available pre-market and post-market trades, not as 24-hour coverage.
- IBKR's API documentation defines `useRTH=0` as allowing data outside Regular Trading Hours and `formatDate=2` as epoch time. The cleaning producer normalizes source timestamps to UTC and uses `America/New_York` for its calendar audit.
- The existing TSM RTH coverage outputs show full expected regular-session coverage in their reported dates. Their status is `COVERAGE_OK_LABEL_UNVERIFIED`; they do not establish bar boundary semantics or a per-bar ETH mask.
- The producer uses the NYSE calendar for trading dates and scheduled early closes. The pinned snapshot does not identify the exact calendar-library/tzdata package version.

### Unknown, therefore blocked

| Field | Status | Why it remains unknown |
|---|---|---|
| 1-minute timestamp label | UNKNOWN | Neither the producer documents nor IBKR's API reference state whether each minute timestamp is the bar start or bar end. Epoch/UTC format does not define that label. |
| `bar_start`, `bar_end` | Unavailable | They cannot be derived without the label convention. |
| `available_at` | UNKNOWN | A historical finalized bar's publication/availability latency is not recorded in the snapshot. |
| Per-bar session | UNKNOWN | RTH and available ETH are requested together, but the normalized snapshot does not provide a verified session label/mask. Boundary classification also depends on the unknown timestamp label. |
| Earliest valid execution | Blocked | A signal may execute only after its information is available and on a later eligible bar. With `available_at` and bar boundaries unknown, the earliest bar cannot be determined. |

Minimum evidence to resolve these items: a producer-owned receipt that pins the exact downloader source commit and request parameters, plus a bounded capture that correlates at least one returned minute bar with its underlying trade interval (or an explicit IBKR statement of minute-bar timestamp semantics). The data owner must also confirm how historical bars become available to the strategy. A per-bar RTH/ETH mask must be produced from the confirmed convention and the versioned NYSE calendar. No wider historical scan is needed.

Official references: [IBKR historical bar request parameters](https://ibkrcampus.com/docs/tws-api/doc/market-data-historical/historical-bars/requesting-historical-bars), [IBKR HistoricalDataRequest formatDate and useRTH](https://ibkrcampus.com/docs/tws-api/protobuf/historical-data-request), and the [legacy bar-data reference](https://interactivebrokers.github.io/tws-api/historical_bars.html). These define request formatting and session filtering but do not resolve the minute label.

## Execution eligibility matrix

| Data/use path | Status | Reason |
|---|---|---|
| Read pinned TSM snapshot through Core `MarketDataClient` | Allowed for bounded diagnostics | Snapshot and producer are pinned; no raw mutation |
| PIT indicator/research calculations using the original UTC timestamp only | Research-only | Do not reinterpret the timestamp as a bar boundary or claim point-in-time availability |
| ExecutionEngine buy | Rejected | `buy_execution_eligible=False`; `TIMESTAMP_LABEL_OR_SESSION_UNKNOWN` |
| ExecutionEngine sell | Rejected | `sell_execution_eligible=False`; `TIMESTAMP_LABEL_OR_SESSION_UNKNOWN` |
| Formal or performance-claim backtest | Not allowed | Timestamp, availability, session mask, costs, and volume participation are unresolved |

This PR also changes unified IBKR provenance to `RESEARCH_ONLY` whenever the returned bars are not all execution-eligible. A data row's `backtest_eligible` flag does not establish that a valid fill can be simulated.

## CostProfile assessment

Agent source: [`ibkr_us_equity_v0.json`](https://github.com/hh4832/strategy-research-agent/blob/feature/m1-core-adapter/governance/cost_profiles/ibkr_us_equity_v0.json). Current source SHA-256: `50149cd2198a49ddb9acadff536f6765e7d938e62bd47e3c40089a561c0f97e5`. It is `DRAFT`; its approval fields are empty.

| Component | Draft value | Basis/status | Core compatibility |
|---|---:|---|---|
| Commission | USD 0.005/share, both sides | ASSUMED; account plan not evidenced | Unsupported lossless mapping: Core currently models equity commission as a notional rate, not per-share |
| Minimum commission | USD 1/order | ASSUMED placeholder | Representable as minimum only, but not sufficient to model the full commission |
| Slippage | 2 bps adverse, both sides | ASSUMED model | Not equivalent to Core's tick-based slippage without an agreed price-dependent conversion |
| Spread | Not supplied | UNKNOWN / unmodelled | No spread crossing model |
| Sell-side regulatory/exchange fees or taxes | Not supplied | UNKNOWN, not zero | No approved values or source |
| Volume/participation cap | Not supplied | UNKNOWN | No evidence to assume all order quantity can fill against bar volume |
| Cost stress | 1x / 2x / 3x proposed | ASSUMED sensitivity grid; not run | Research design only |

No cost component is `BROKER_ACTUAL` or approved `OFFICIAL` for this account. The draft profile must not be labelled approved or used as formal cost. Research lead approval and an account pricing statement are needed for actual commission; the cost schema must also support per-share commission, spread, and a volume participation rule before an execution probe can be considered cost-valid.

## Data Client and Claude handoff

Existing API remains compatible:

```python
from market_research_core import MarketDataClient, MarketDataConfig

client = MarketDataClient(MarketDataConfig(
    ibkr_run_dir="/drive/.../20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL",
))
bars = client.load(
    market="IBKR",
    symbol="TSM",
    start="2024-01-02T14:30:00Z",
    end="2024-01-05T21:00:00Z",
    usage="RESEARCH_ONLY",
)
```

The bounds above are illustrative only; the actual research run must predeclare its finite UTC interval. The returned frame retains the source `timestamp`, `timezone="UTC"`, and provenance. While unresolved, the contract fields remain `bar_start=NaT`, `bar_end=NaT`, `available_at=NaT`, `session="UNKNOWN"`, both side eligibility flags are false, and rejection reason is `TIMESTAMP_LABEL_OR_SESSION_UNKNOWN`. Provenance scope is `RESEARCH_ONLY`.

Claude may use this API for snapshot verification and bounded data diagnostics. It must not call `ExecutionEngine.run()` for TSM, synthesize `bar_start`/`bar_end`/`available_at`/session, or treat a data-quality `GO` as execution approval. A real-data execution probe is deferred until the minimum evidence and cost approvals above exist. No probe output was generated, so no Drive run archive was created.

For a future valid execution run, the signal must be formed only after its source bar's confirmed `available_at`; order creation must follow that time; the earliest execution is a later bar that is eligible for the order side and within the stated session/TIF. Same-bar execution is forbidden, including at that bar's open.

## Regression and CI

- Added a regression asserting that TSM unified bars with unknown timestamp/session semantics keep both sides ineligible and provenance `RESEARCH_ONLY`.
- The base-main CI passed before this change. The feature-branch GitHub CI must pass before this report is treated as delivered.
- No local pytest was run: this workspace has no repository checkout and lacks `pyarrow`; Python 3.11 is supplied by the repository's CI workflow.

## Decision and required owner actions

**NO-GO for formal IBKR TSM execution/backtesting. SCOPED_GO for pinned-snapshot, read-only research diagnostics only.**

To revisit the gate, supply: (1) the bounded timestamp-to-trade interval evidence and producer receipt, (2) the confirmed available-time rule and per-bar session mask, (3) actual account commission evidence or explicitly approved assumptions, (4) a cost model for per-share commission/spread/volume participation, and (5) research lead approval of the profile. Until then execution remains fail-closed.
