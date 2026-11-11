# Continuous dated trading costs v1 — Core Phase 5-C1

Status: **CAPABILITY_IMPLEMENTED / DATA_PENDING. No formal GO.**

This extends the existing ExecutionEngine, not a second engine. Strategy,
orders, temporal/PIT policy, native-data contracts, snapshots, market readiness,
Holdout, authorizations and budget records are unchanged. No conversion, Data Lake
validation, raw Holdout read or formal strategy run is part of this delivery.

## Cost configuration inventory and missing history

Core's `configs/cost_profiles/ibkr_fixed_equity.json` is still the original
configuration: applicability begins 2025-01-01, its regulatory rate is NULL and
basis UNKNOWN. It is not usable evidence for 2017–2024. No complete TAF history
exists in Core. The prior real-probe diagnostic mentions a 2024 SEC version/rate
and a TAF value; a diagnostic is not a source-verified historical fee table and
is not promoted to one here. Original configuration and diagnostic bytes remain
unchanged.

| Component | Source-verified loadable coverage in Core | Missing required trading dates |
| --- | --- | --- |
| SEC Section 31 | None | 2017-01-01 through 2024-12-31 inclusive |
| FINRA TAF | None | 2017-01-01 through 2024-12-31 inclusive |

Required official evidence:

- SEC Section 31 fee-rate advisories/effective-date notices, including the rate
  already effective at 2017-01-01, every subsequent change through 2024-12-31,
  and enough successor information to establish interval endings. Record the
  coefficient in USD fee per USD notional, not the quoted dollars-per-million
  figure without conversion. Primary authority: `https://www.sec.gov/`.
- FINRA Trading Activity Fee schedules, Schedule A/Section 1 rule material,
  regulatory notices and rule filings establishing the predecessor rate and
  all changes through 2024-12-31. Primary authority:
  `https://www.finra.org/rules-guidance`.
- For each record: inclusive effective dates, version, primary-source URL,
  reviewed numerical rate and unit, SHA256 of the actual source document,
  recorded verification status and canonical record SHA. Archive the source
  documents and independently reviewed complete schedule SHA.

No official publication/rate history was downloaded or certified in this task.
No historical numeric rate is guessed, extrapolated, or filled from the 2024
model. `configs/research/tsm_dated_costs_data_pending.json` records these gaps;
it is a readiness report, not a loadable cost schedule. Warm-up bars without
candidate orders require no invented 2016 fill fee. If actual fills/candidates
or the declared execution window reach 2016, that coverage must also be supplied.

## Versioned, pinned, immutable input

`market_research_core.dated_costs` provides:

- `rate_record_sha256(record)` — canonical sorted compact JSON with
  `record_sha256=null`, NaN/infinity forbidden.
- `schedule_sha256(document)` — the same rule with `schedule_sha256=null`.
- `DatedCostSchedule.from_document(..., expected_sha256=..., scenario=...,
  source_documents=...)` and `load_dated_cost_schedule(path, ...)`.
- `new_york_fill_date(timestamp)` — rejects naive/unknown timestamps.

The exact JSON fields are:

```text
contract: "tsm-dated-trading-costs-v1"
schedule_id, version, schedule_sha256
scope: "RESEARCH_ONLY"
evidence_kind: "VERIFIED_SOURCE_RECORDS" | "SYNTHETIC_FIXTURE"
symbol: "TSM"; market: "IBKR_EQUITY"; timezone: "America/New_York"
coverage: {start: YYYY-MM-DD, end: YYYY-MM-DD}       # inclusive
base_profile: complete CostProfile dataclass fields
base_profile_sha256: CostProfile.sha256
scenarios: the exact four definitions below
sec_rates: [rate record, ...]
taf_rates: [rate record, ...]
```

Every rate record has exactly:

```text
version, effective_from, effective_to                 # inclusive ISO dates, no open-ended interval
rate                                                 # finite nonnegative decimal STRING, never NULL
unit: "USD_PER_NOTIONAL" (SEC) | "USD_PER_SHARE" (TAF)
source_ref, source_basis, verification_status
source_document_sha256, record_sha256
```

SEC and TAF intervals are validated independently. Neither may overlap or have
any gap, and each must span all declared coverage, including non-trading dates.
Real source intervals may start earlier/end later than requested coverage; the
resolved profile uses their intersection with coverage. No source interval is
extended. SEC/TAF revisions on different dates are supported. Rates, including
TAF+CAT, must be exactly representable under the existing Decimal(str(float))
CostProfile contract. Duplicated JSON keys, unknown keys, malformed bounds, missing
rates, invalid units/versions, wrong record/base/schedule SHA or a changed scenario
are refused. An externally reviewed expected SHA is mandatory; copying a file's
self-declared hash is not independent trust.

Official mode requires OFFICIAL/VERIFIED records and HTTPS primary SEC/FINRA
citations (exact `sec.gov`/`www.sec.gov` or `finra.org`/`www.finra.org` authority).
`source_documents` must map every required source-document SHA to an explicit
local file; all bytes are independently hashed on construction. Extra/missing
sources or wrong bytes fail closed. No URL is fetched, no directory discovered,
and no market file is inferred. These checks prove configuration/source-byte
integrity, not that a human-transcribed rate matches its cited publication:
independent source review is still required. SOURCE VERIFIED is not broker/account
cost approval.

Synthetic mode requires ASSUMED/SYNTHETIC_FIXTURE records with an explicit
`SYNTHETIC_FIXTURE:` source label. Engine accepts this mode only with a
`SYNTHETIC_...` dataset identity and RESEARCH_ONLY scope. It never establishes
historical data readiness or authorizes a real run.

The object stores a frozen canonical JSON snapshot; exposed dictionaries and
profiles are fresh copies. Mutating caller inputs or moving an evidence file
cannot change a previously verified schedule. Reloading altered source bytes
requires verification again. No live file lookup occurs per fill.

## Same model, four scenarios

| Scenario | Slippage per side | CAT per share per side |
| --- | --- | --- |
| BASE | 2 bps | USD 0.00005 |
| LOW | 1 bp | USD 0.00005 |
| STRESS | 4 bps | USD 0.00005 |
| CAT_STRESS | 2 bps | USD 0.00010 |

The base is a pinned DRAFT/ASSUMED EQUITY FIXED template: USD .005/share,
minimum USD1, 1% notional cap, .01 tick, half-spread0, HALF_UP fee/tax rounding.
Volume-participation settings stay exactly those of that template. Conflicting
sell taxes, exchange/clearing charges, proportional commission, tick slippage,
ALL_IN mode and daytrade qualification are refused. New rate records replace
only the SEC and combined per-share regulatory rates; they do not add a second
charge. Existing legacy base-rate dates/values are template provenance, never
historical evidence or a fallback for missing new dates.

SEC remains **SELL-only**. TAF and CAT remain **BOTH-side, uncapped**, with the
existing conservative TAF application and assumed CAT pass-through. CAT is not
asserted broker-actual or a confirmed upper bound. A resolved profile is always
DRAFT/ASSUMED; component approval stays PENDING. Formal cost/market gates are not
upgraded even when regulatory source bytes/rates are verified.

## One continuous engine run

```python
from market_research_core import ExecutionEngine, load_dated_cost_schedule
from market_research_core.tsm_research_costs import tsm_cost_diagnostics

schedule = load_dated_cost_schedule(
    "reviewed_tsm_cost_schedule.json",
    expected_sha256=INDEPENDENTLY_REVIEWED_SCHEDULE_SHA,
    scenario="BASE",
    source_documents=REVIEWED_SOURCE_SHA_TO_LOCAL_FILE,
)
result = ExecutionEngine().run(
    unchanged_continuous_bars, unchanged_orders,
    cost_profile=schedule.base_profile, cost_schedule=schedule,
    config=existing_research_only_execution_config,
)
report = tsm_cost_diagnostics(result, schedule)
```

`cost_schedule` is optional. If omitted, the original CostProfile route, hashes,
columns, effective-date guard and financial behavior remain unchanged. No caller
may omit its cost profile or substitute a base with a different SHA.

With a schedule, the declared half-open execution window and every potential
candidate fill date are checked before the event loop changes cash or lots.
Each candidate's **actual execution timestamp** is converted to New York date,
independent of the engine output timezone. This selects the intersecting SEC/TAF
interval and fixed scenario, then the existing `adverse_fill_price` and
`calculate_fill_costs` do all debits. Signal/order creation dates, UTC dates and
settlement dates do not choose rates. A delayed fill can therefore use a new
rate. SMA/orders are not generated or recomputed here. Cash, FIFO lots, pending
targets and the equity timeline are initialized once; there is no rate-boundary
close, cash reset, state reset or stitched backtest. This is still one scenario
run; the Agent's four scenarios remain one authorization batch, not four trials.

## Ledger, provenance, and reconciliation

All existing monetary fields and aliases are retained. `regulatory_fee` remains
rounded SELL SEC plus the separately rounded combined TAF+CAT line. `fee` aliases
commission+exchange+clearing; `tax` aliases transaction_tax. Never debit aliases
again. `price_adjustment_cost` is already in fill price/PnL, not an extra debit.

Each scheduled fill adds schedule id/version/SHA, scenario, NY cost-effective
date and SEC/TAF source-record SHAs. Its existing `cost_profile_sha256` is the
actual resolved profile SHA; intervals/scenarios can have different profile SHAs.

Dated result provenance records schedule id/version/SHA/scenario/timezone/
coverage/evidence kind and the complete pinned schedule, plus the set of applied
profile SHAs. `cost_profile_sha256` / `base_cost_profile_sha256` at **result** level
identify the input template, explicitly labelled `BASE_TEMPLATE_ONLY`, not a
single rate for all fills. Engine version is 2.3.0 for the new route; the legacy
route retains its old version/shape. Agent validators must understand this
separation instead of requiring every fill SHA to equal the template SHA.

`tsm_cost_diagnostics(result, schedule)` reconstructs each selected profile and
reuses the original one-fill debit verification/allocation. It verifies schedule,
profile, source-record and date pins, all monetary aliases and actual debits.
TAF gets the original 12-decimal pro-rata allocation of the already-rounded
combined line; CAT gets the exact residual. Sum SEC+TAF+CAT equals the Engine's
actual regulatory debit, including rounding. There is no second fee calculator
or second deduction. The single-profile diagnostics API remains unchanged.

## Validation and next Agent integration

Synthetic tests cover all four single-interval results against the old route,
independent SEC/TAF revisions, creation-vs-fill date, NY date vs Taipei/UTC,
partial exits/FIFO, an eight-year held position, completed-bar SMA5/20 target
orders carried across a fee boundary, identical order events/fill quantities and
cash/equity differences explained solely by actual cost debits. Integrity tests
reject gaps/overlaps, NULL/unknown/unverified rates, wrong sources, changed source
bytes or pins, altered ledger/provenance and synthetic rates on real identities.
Legacy execution, cost, temporal and 2024-month regression tests remain required.
No fixture is official history or real strategy evidence.

Agent PR #21 is not changed in this task. Its next separately reviewed integration
must pin this Core commit and a complete reviewed historical schedule/source set,
replace its hardcoded `CORE_CROSS_RATE_UNSUPPORTED` and single-interval CostSet
precheck with actual schedule/coverage validation, and pass one schedule per fixed
scenario into the existing continuous Engine call. Keep its one SMA/order stream,
all governance/GitHub/one-batch/receipt/PIT/Holdout gates and original producer pin.
Update ledger/adapter/evaluator/archive validation for per-fill profile SHA and
result-level template vs schedule SHA; use schedule-aware diagnostics. Archive
cost/source pins with results. Neither a Core upgrade nor synthetic PASS supplies
missing historical data, formal authorization or permission to run research.
