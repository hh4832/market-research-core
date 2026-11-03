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


## M3-A.2 bounded timestamp and closing-auction verification

### Evidence reviewed

- Pinned snapshot and Drive file ID remain `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL` / `1B_2WyFRHXUzwg3C1BR7GLApGl722Ezli`; Drive metadata reports `tsm_normalized.parquet` size 29,363,599 bytes. No data file or FINAL GATE artifact was changed.
- The existing daily RTH coverage CSV reports 390/390 candidate rows on many normal sessions, but labels them `COVERAGE_OK_LABEL_UNVERIFIED`; it does not expose the actual first/last bar timestamps.
- At producer SHA `06359faf7ef4b8ff702a7ab1a6d07521d3b68f28`, `src/ibkr_qc/validate.py::daily_coverage` explicitly says it assumes bar-start timestamps and that coverage is not label validation. It computes candidates in `[market_open, market_close)`; the resulting 390 count is therefore not independent evidence of the timestamp label.
- The read-only Drive connector can fetch text diagnostics and report file metadata, but it has no row/column projection API for Parquet. Fetching the binary file through this interface would transfer the entire 29,363,599-byte snapshot, outside the requested bounded access. No raw Parquet row was claimed or fabricated.

### Verdict

- **First RTH bar / last RTH bar / 390 actual bars on the seven requested dates:** not reverified in this environment. The prior report of 09:30–15:59 is not accepted as evidence because it lacks reproducible row output.
- **Timestamp label:** UNKNOWN. The producer's coverage implementation is explicitly provisional. Neither UTC epoch conversion nor IBKR's documented `formatDate` and `useRTH` parameters specify bar start/end semantics.
- **Closing-auction attribution:** UNKNOWN. The fixed OHLCV Parquet has no trade-condition or individual trade-time evidence. A 15:59 or 16:00 volume spike cannot identify the auction print. No independent official closing-price series was available for comparison.
- **RTH close boundary:** FAIL-CLOSED. Do not simulate a closing-auction order or treat the unverified close as an executable auction price. Keep ETH fills disabled.

### Reproducible bounded extraction

Added `src/market_research_core/ibkr_tsm_audit.py`. It reads only `timestamp_utc, open, high, low, close, volume` for the seven predeclared New York dates using a PyArrow dataset predicate. It writes:
- `bounded_timestamp_evidence.csv`: first/last timestamps and counts under both start-label and end-label hypotheses, per-day missing-minute counts, pre/after-hours timestamp observations, and normal-session status.
- `closing_auction_comparison.csv`: actual source rows at 15:58, 15:59, 16:00, 16:01 and 16:02 NY time, with OHLCV and volume ratios. Auction attribution and official-close comparison remain explicitly UNKNOWN without those independent sources.
- `run_info.json`: fixed snapshot ID, producer SHA, Drive file ID, research commit SHA, selected dates and columns.

From a Colab session with the existing Drive folder mounted, check out this PR branch, install the repository's declared dependencies, then run:

```bash
python -m market_research_core.ibkr_tsm_audit \
  --parquet "/content/drive/MyDrive/<existing snapshot path>/tsm_normalized.parquet" \
  --output-dir "/content/drive/MyDrive/Quant_Research/market-research-core/M3A2_<new-run-id>"
```

The output directory must be new or empty; the script refuses to overwrite results. Its pattern verdict is intentionally limited to `STRONGLY_SUPPORTED_NOT_CONFIRMED` or `UNKNOWN`; a full 390-row grid cannot independently confirm timestamp semantics. Synthetic tests cover both start/end patterns, the ambiguous 16:00 row, missing requested dates, and auction-data limitations. No real-data output CSV is present because this environment cannot execute a filtered read of the private Drive Parquet without transferring the whole file.

### Smallest remaining evidence

One bounded run of this committed script against the existing mounted Parquet, preserving both CSVs and `run_info.json`, is required to establish the seven dates' actual first/last rows. To attribute a closing auction, the same date sample must additionally include a trade-level source with timestamps and sale/auction conditions (or an authoritative provider mapping of the auction print to its minute bar) and an independent official close. Without that trade-level evidence, auction attribution remains UNKNOWN even if the 15:59/16:00 OHLCV rows are present.
