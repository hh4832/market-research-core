# M3-A.4 FINAL — Core engineering contract v1.0.0

Core PR #3, branch `feature/m3a-ibkr-tsm-readiness`. Baseline `c1a3661d127c7139ee86d824d1a380edf3d2e114`. Agent acceptance reference `6309a732470c79600ff37d551adeeb9f194bdc30`; Agent source is not changed by this work. Engine API version `2.2.0`; existing engine, FIFO and legacy Oracle inputs/expectations retained.

## Engineering and market decisions

Temporal engineering and cost calculation verdicts derive from the final synthetic regressions and CI, recorded in `docs/diagnostics/m3a4_final.json`. No real-data engine run, market download or historical FINAL GATE rerun occurs. The user reports M3-A.3 seven-date extraction is complete; it is not repeated or reinterpreted here. Dataset quality GO is independent of timestamp label UNKNOWN, auction attribution UNKNOWN, and formal execution NO-GO.

`CONSERVATIVE_LABEL_AGNOSTIC` is an **explicit research simulation**, never FORMAL_ELIGIBLE. It cannot establish measured IBKR publication latency or source label semantics. Actual market use still requires research-owner acceptance, approved assumptions/costs and Agent governance/holdout approval. The default MarketDataClient TSM gate remains unchanged and fail-closed; this is a separate opt-in API over caller-supplied bounded native rows.

## Exact temporal API

```python
from market_research_core import (
    CONSERVATIVE_POLICY, TemporalPolicy, prepare_conservative_bars,
    ExecutionConfig, ExecutionEngine, ExecutionOrder,
)

bars = prepare_conservative_bars(source_rows, bar_minutes=15)
config = ExecutionConfig(
    initial_cash=100_000,
    bar_label="UNKNOWN", session_semantics="CONFIRMED",
    timestamp_timezone="America/New_York",
    available_at_policy="CONSERVATIVE_LABEL_AGNOSTIC",
    temporal_policy=CONSERVATIVE_POLICY,
    # Pin actual caller inputs; do not substitute UNKNOWN for formal evidence.
    core_commit_sha=actual_core_sha, dataset_snapshot_id=snapshot_id,
    dataset_producer_sha=producer_sha, spec_sha256=spec_sha,
    permitted_usage_scope="RESEARCH_ONLY",
)
result = ExecutionEngine().run(bars, orders, cost_profile=profile, config=config)
```

`source_rows`: pandas DataFrame with `source_timestamp` (timezone-aware exact native minute labels), `open, high, low, close, volume, symbol, contract`. Initial temporal scope is a single TSM symbol/contract, native 1m inputs. No timestamp column is overwritten; normalized source instants are carried in UTC. Input duplicates, nonfinite/inconsistent OHLCV, naive timestamps and unsupported aggregation sizes fail. Caller remains responsible for the true source identity and dataset pins.

For each source label **t**:

| Hypothesis | Possible interval | Completion | Candidate open |
|---|---|---|---|
| START | [t, t+1m] | t+1m | t |
| END | [t-1m, t] | t | t-1m |

For n adjacent native labels t0..tn, `completion_earliest=tn`, `completion_latest=tn+1m`, `modeled_available_at=completion_latest`. The normalized execution envelope uses `bar_start=t0-1m` (earliest possible open), `possible_open_latest=t0`, and `bar_end=available_at=modeled_available_at`. Earliest candidate open must be **strictly later** than both signal availability and order creation. Thus execution occurs after completion under either label interpretation; equality never fills. Actual publication latency is UNKNOWN.

### Fifteen-source-bar aggregation

`bar_minutes=15` groups consecutive source labels into fixed 15-label buckets. Each requires exactly all 15 native minute labels in its bucket, positive volumes and both-hypothesis RTH coverage. OHLCV is first open / maximum high / minimum low / last close / summed volume. Missing rows are never reindexed, forward-filled or reconstructed. Incomplete groups are retained with `complete_source_bucket=False`, ineligible for signals and execution.

This is an uncertainty envelope for 15 source bars: its possible wall-clock support is **16 minutes**. It is not a claim of confirmed exchange-aligned 15m START/END candles. No rounding of a source label silently establishes its meaning. The first/last retained source timestamp, count and native source records are carried for verification.

The engine recomputes envelopes, aggregation, modeled availability, policy pins, signal/side masks and OHLCV from carried `source_rows`; changed timing/masks or aggregation fail validation. Full known-answer tests separately apply START and END hypotheses and check completion <= modeled signal availability < earliest fill open <= either actual open. A perturbation to later OHLCV leaves the earlier fill unchanged.

### Signals, candidate bars and eligibility

- Signals must identify a row by `ExecutionOrder.signal_time == row.modeled_available_at`; `signal_available_at >= signal_time`, `order_time >= signal_available_at`. This binds every signal to an already completed native group. Core does not generate signals or inspect how the caller calculated a strategy.
- `signal_eligible` requires positive source volumes, a complete bucket, both-hypothesis RTH coverage and modeled observation time **10:00–15:30 America/New_York**. The policy permits narrowing, never widening, this initial window.
- NYSE calendar `pandas-market-calendars==5.5.0` supplies DST, holidays and early closes. The entire uncertainty envelope must be RTH; candidates whose latest possible completion reaches the session close are excluded. Unknown auction prices/prints are never used. ETH is never eligible.
- `buy_execution_eligible` / `sell_execution_eligible` mean **research-model candidate eligibility only**, with `execution_eligibility_scope=RESEARCH_ONLY`. They are not formal market approvals. The modeled source label remains UNKNOWN.
- Orders are MARKET only in this mode. LIMIT is rejected because intrabar high/low timing is unconfirmed. No same-timestamp override. DAY/NEXT_BAR execution retains the existing engine's TIF and rejection behavior, with candidates confined to the signal's NYSE session. Zero-volume or over-cap bars are ex-post execution invalidations, not inputs available to a signal at the candidate open.
- `TemporalPolicy` fields: `policy_id`, `version`, `source_minutes`, `signal_start_et`, `signal_end_et`, `calendar_version`, `assumption`. `sha256` hashes sorted compact JSON of all fields. Current policy ID `CONSERVATIVE_LABEL_AGNOSTIC`, version `1.0.0`.

## Exact cost API and configuration

`CostProfile` retains all M3-A.1 fields and legacy default hashes. `calculate_costs(price, quantity, side, profile)` remains `(combined_fee, tax_plus_regulatory_fee)` in Decimal. `calculate_cost_components(...)` remains `(combined_fee, transaction_tax, regulatory_fee)`. New `calculate_fill_costs(...)` returns Decimal `commission, exchange_fee, clearing_fee, transaction_tax, regulatory_fee`. `combined_fee = commission + exchange_fee + clearing_fee`; never debit both components and combined_fee.

Additional optional fields:

| CostProfile field | Calculation / constraint |
|---|---|
| `commission_cap_rate` | Equity FIXED only: min(max(minimum, per-share or notional commission), notional × cap). Component evidence/basis/approval required. |
| `commission_scheme` | `FIXED` or legacy None; `TIERED` is explicitly unsupported and rejected, never treated as Fixed. |
| `fee_mode` | `ALL_IN` forbids separate exchange, clearing and regulatory amounts; `COMPONENTIZED` permits them. Taxes remain separate from all-in commission. |
| `exchange_fee_per_unit`, `clearing_fee_per_unit`, `regulatory_fee_per_unit` | quantity × component rate; positive configured components require their own evidence/basis/approval. |
| `configuration_metadata` | Original configuration SHA, applicability, selected symbol/date and regulatory version, qualification evidence. Included in profile SHA/provenance. |

Existing per-share/notional commissions are mutually exclusive; tick/bps slippage is mutually exclusive. Spread adds only the configured half-spread (example equity uses zero to avoid double counting). All prices round adversely to tick size. Futures transaction tax uses **each fill's price × quantity × multiplier × rate**. All-in commission and componentized fees cannot coexist. No example rate is hard-coded into calculation logic.

```python
from market_research_core import load_cost_profile, configuration_sha256
profile = load_cost_profile("configs/cost_profiles/txf.json", symbol="TXF", as_of="2025-02-03")
```

JSON and safe YAML are equivalent (PyYAML declared dependency). Each configuration has `schema_version=1.0.0`, `profile`, `applicability`, `regulatory_schedule`, `configuration_sha256`. Unknown top-level keys, unsupported profile constructor fields, missing metadata and bad SHA fail closed. Canonical configuration SHA: sorted compact JSON with `configuration_sha256=null`, NaN/infinity forbidden. `profile.sha256` is a separate hash of the fully resolved profile, selected dated fee assumptions and applicability. Rate changes require a new configuration version/SHA; caller pins both configuration and resolved profile SHA.

Examples:

| File in configs/cost_profiles | Explicit assumption |
|---|---|
| `ibkr_fixed_equity.json` | TSM/SOXX Fixed: USD .005/share/side, minimum USD1, cap 1% notional, 2bps slippage, half-spread0, explicitly assumed participation cap1%. **Regulatory rate is NULL/PENDING**; fill in a dated, sourced schedule and regenerate config SHA before loading. Missing rate never becomes zero. |
| `ibkr_nq.json` | USD2.50/contract/side ALL_IN, multiplier20, tick .25 (USD5), 1-tick slippage, stress0/1/2. |
| `ibkr_es.json` | USD2.50/contract/side ALL_IN, multiplier50, tick .25 (USD12.50), 1-tick slippage, stress0/1/2. |
| `taiwan_equity.json` | Assumed commission .05%/side; listed .1425% retained as reference, not added to it; ordinary sell tax .3%; qualified .15%; 5bps slippage, stress2/5/10. |
| `txf.json` | NT$50/contract/side, dynamic tax .00002/side, multiplier200, tick1, slippage1tick, stress0/1/2. |

All examples are **ASSUMED / DRAFT / UNVERIFIED**. Effective dates in examples bound research assumptions, not verified statutory/broker fee histories. Stress metadata is a list for explicit scenario construction; it does not execute a search or modify rates. Change the active CostProfile field and version/SHA for a different assumption. The 1% cap only applies to selected Fixed, never arbitrary/tiered accounts. Attempted official source `https://www.interactivebrokers.com/en/pricing/commissions-stocks.php` returned HTTP403 in this environment; cap and other rules have **not** been independently verified. Do not claim OFFICIAL or actual account rates.

Regulatory schedule entries require version, inclusive effective dates, rate, basis, evidence and approval. Loader selects exactly one matching rate and rejects missing/overlapping intervals. Engine rejects fills outside its selected effective interval; split a run across rate revisions explicitly rather than applying stale rates. Configured profiles also bind market/symbol.

Taiwan ordinary .3% sell tax is the default. `daytrade_evidence` must explicitly contain `same_day_offset=True`, `instrument_eligible=True`, `account_eligible=True`, `evidence_ref`. These are assertions/evidence supplied by the caller, not an automatic inference from strategy frequency. Engine additionally requires actual same-day long lots and matching qualification date for discounted sells; overnight exits, opening shorts or excessive quantities are rejected (`DAYTRADE_TAX_NOT_QUALIFIED`). This research contract does not approve statutory/account eligibility.

## Exact Core→Agent fields

`ExecutionResult`: existing `fills, round_trips, rejections, equity_curve, open_positions, invalidated_round_trips, order_events, provenance, status`. New read-only `ledger` property returns a copy of fills: one row per executed order, not a second simulation.

| Output | Exact fields |
|---|---|
| Fill / ledger identity | `order_id, symbol, market, contract, strategy_id, experiment_id, session, side, qty` |
| Fill timing | `signal_time, signal_available_at, order_time, exec_ts, execution_assumption` |
| Conservative-only source timing | `source_timestamp` (candidate first source label), `signal_source_timestamp`, `source_timestamp_label=UNKNOWN`, `modeled_available_at` (signal bucket completion), `candidate_modeled_available_at`, `candidate_possible_open_latest` |
| Conservative-only policy | `temporal_policy_id, temporal_policy_version, temporal_policy_sha256, temporal_assumption, execution_eligibility_scope=RESEARCH_ONLY` |
| Fill pricing/costs | `raw_price, fill_price, fee, tax, regulatory_fee, commission, exchange_fee, clearing_fee, transaction_tax, explicit_cost, commission_scope, price_adjustment_cost, slippage_cost, spread_cost, tick_rounding_cost` |
| Cost pins | `cost_profile_id, cost_profile_version, cost_profile_sha256` |

`explicit_cost = fee + tax + regulatory_fee`, and `fee = commission + exchange_fee + clearing_fee`. `transaction_tax` aliases `tax`. Do not sum aliases twice. `price_adjustment_cost` is the fill-price impact and is **already in fill_price/PnL**; do not debit it again. `slippage_cost + spread_cost + tick_rounding_cost = price_adjustment_cost`. Residual tick/limit rounding attribution is signed; legacy LIMIT clipping may reduce intended price impact. Conservative mode does not support LIMIT.

Existing FIFO allocates combined fees, taxes and regulatory fees proportionately on partial exits; open-lot remaining costs are retained. Ledger component rows are per fill; round-trip `net_pnl` includes matched entry/exit allocated costs. `equity_curve` retains `timestamp,cash,realized_net_pnl,unrealized_pnl,margin_requirement,equity`; no artificial end-of-data liquidation.

Provenance retains engine/dataset/spec/parameters/seed/cost multiplier pins, and adds `cost_profile_version`, `cost_configuration`, `fee_mode`. Conservative provenance adds `temporal_policy_id/version/sha256`, full `temporal_policy`, `source_timestamp_label=UNKNOWN`, `modeled_available_at_rule=LATEST_START_END_COMPLETION`, `execution_eligibility_scope=RESEARCH_ONLY`. `engine_git_sha` is the actual caller-supplied execution code pin; Agent must obtain and compare it against the installed Core, not invent it.

New order-level rejection codes: `UNSUPPORTED_CONSERVATIVE_ORDER_TYPE`, `COST_RATE_OUTSIDE_EFFECTIVE_DATES`, `COST_PROFILE_MARKET_MISMATCH`, `DAYTRADE_TAX_NOT_QUALIFIED`. Existing `PIT_VIOLATION`, `ZERO_VOLUME_BAR`, `VOLUME_PARTICIPATION_EXCEEDED`, `BAR_NOT_ELIGIBLE`, `OUTSIDE_SESSION`, expiry/risk and side eligibility codes remain. Preparation exposes `INCOMPLETE_SOURCE_BUCKET`, `OUTSIDE_CONSERVATIVE_RTH`, `CLOSING_AUCTION_EXCLUDED`, `ZERO_VOLUME_BAR` on rows; an ineligible bucket can surface as `BAR_NOT_ELIGIBLE` during execution. Malformed/tampered frame or unsupported cost config raises ValueError/TypeError before a result; Agent records a failed contract, never fabricates a fill.

## Minimal next Agent acceptance (no parallel engine)

After reviewing this exact Core commit, Agent maps policy/config/pins to the released API above and adds the new reviewed rejection codes. Test one tiny complete synthetic source bucket through its existing adapter, verify ledger and profile/policy/engine pins, intentionally tamper one pin/unknown component and require failure, then check R02/manifest and unchanged formal Registry/Holdout. Do not upgrade real TSM market eligibility based on this engineering GO. Real source label and auction attribution remain UNKNOWN.

## Notion handoff

This contract and `docs/diagnostics/m3a4_final.json` are prepared Phase3 handoff content. Actual Notion update is PENDING: no Notion connector exists in this session. No Notion page was claimed updated. Stop after M3-A.4; no M3-A.5, data validation project or new feature phase is opened.

## Final engineering acceptance

**GO for conservative research timing and Fixed/componentized cost calculation.**
Implementation `15ed2d934db8b772557548eab3cc8cafe1d8db3e`: Python 3.11.17 and 3.12.14
local full regressions each 84 PASS, zero failed/skipped, coverage84%.
[PR CI run 41967446695](https://github.com/hh4832/market-research-core/actions/runs/41967446695)
completed both Python3.11 and3.12 jobs successfully. The closure commit updates
only this acceptance record and machine diagnostics; it does not add a feature.

**Formal execution remains NO-GO.** Owner acceptance, dated/account-approved fees
(including the missing IBKR regulatory rate and unverified Fixed cap rule),
holdout approval and minimum Agent adapter acceptance remain external conditions.
Tiered commission is unsupported and fails closed. Core API is documented and
ready for the bounded Agent acceptance, not a claim that the Agent already
supports it. No Notion connector is available; the handoff remains prepared here.
