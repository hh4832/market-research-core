# Phase 4-A — TSM RESEARCH_ONLY adapter contract v1.0.0

Baseline `699e3ad59951bbebb769bb69170be8d9a1a96c01`; separate branch
`feature/phase4a-tsm-native-research-adapter`. Phase3 PR #3 is not changed by
pushing this branch. It was still Open when inspected; a Phase4 PR should use
its branch as the explicit dependency/base until Phase3 is merged. No main
modification, reset or merge is performed.

**Current verdict: SCOPED_GO.** Synthetic implementation/regression checks pass.
Actual snapshot manifest, Parquet schema/hash, trusted integrity receipt,
Holdout boundaries and existing SEC/TAF records have not been supplied in this
session. No actual native snapshot filename/path/schema/SHA or numeric SEC/TAF
rate has been guessed. Real bounded conversion is **PENDING**, not PASS.
The snapshot identity/history are the user's fixed scope, not new data evidence.

## Native imports and exact call

```python
from market_research_core.tsm_native import (
    DateWindow, ManifestLayout, FileIntegrityReceipt,
    load_native_tsm, core_code_identity, NativeSnapshotRejected,
)

# Values come from the ACTUAL existing manifest and an independently trusted receipt.
layout = ManifestLayout(**observed_json_pointers)
receipt = FileIntegrityReceipt(**trusted_receipt_document)
loaded = load_native_tsm(
    snapshot_path=caller_snapshot_path,
    manifest_relative_path=observed_manifest_relative_path,
    expected_manifest_sha256=pinned_manifest_sha256,
    layout=layout,
    integrity_receipt=receipt,
    expected_receipt_sha256=pinned_receipt_sha256,
    symbol="TSM", dates=DateWindow("2024-06-03", "2024-06-28"),
    research_window=authorized_research_window,
    holdout=tuple(explicit_holdout_windows),
    max_rows=100_000,
)
print(core_code_identity())  # actual installed Core SHA; no guessed release SHA
native = loaded.native
source_1m = loaded.source_1m
conservative = loaded.conservative_15m
provenance = loaded.provenance
```

No Colab, Drive, local user-directory or Parquet filename is hard-coded. Dates
are inclusive NY calendar dates, within `2016-10-19`–`2026-10-16`. The requested
window must be <=31 dates and within caller-authorized research dates. An
explicit nonempty Holdout list is mandatory. Overlap is rejected **before any
path access**. No implicit warmup extension or Holdout inference exists.

### Manifest binding

`ManifestLayout` contains seven explicit JSON pointers into the EXISTING JSON
manifest: `snapshot_id`, `producer_sha`, `symbol`, `parquet_path`,
`parquet_sha256`, `columns`, `arrow_schema`. Pointers support JSON object/list
traversal and `~0`/`~1` escaping; there are no default pointers or filename searches.

- Root basename and manifest ID must equal
  `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`.
- Producer must equal `06359faf7ef4b8ff702a7ab1a6d07521d3b68f28`; symbol TSM.
- Manifest bytes must match the caller's pinned SHA256. The manifest itself
  selects the confined relative Parquet path and its expected SHA256. Absolute
  paths, parent escapes and symlinks escaping the root are rejected.
- `columns` must resolve to a dict with exactly logical roles
  `timestamp, open, high, low, close, volume`, mapped to six distinct physical
  column names. No native column alias is guessed.
- `arrow_schema` must resolve to the complete physical Arrow schema mapping
  `{native_column_name: str(arrow_field.type)}`; it must match the footer.
  The timestamp must be an Arrow timestamp carrying its original timezone.
- Actual manifest compatibility is not yet established. If it lacks the required
  evidence/metadata or uses another format, fail closed and review a binding
  based on that actual manifest; do not manufacture source evidence or edit it.

### Integrity without implicit full-file scanning

`load_native_tsm` does **not** rehash the whole Parquet. It requires a trusted,
caller-pinned `FileIntegrityReceipt` containing `parquet_sha256`,
`manifest_sha256`, `stat` and `verification_ref`. `stat` records device, inode,
size, mtime_ns and ctime_ns from the verified local file; all must still match.
Receipt SHA uses sorted compact JSON of those fields. File/manifest pins, receipt
pin and unchanged file stat must all agree. The file/manifest are checked again
for changes after the bounded read. Stat is a stale-receipt guard, **not a new
cryptographic verification**; caller trust in the original receipt is essential.
Moving/restoring a file to a different inode invalidates this receipt. This is
not an adversarial filesystem or remote object-store attestation protocol.

An optional separate producer is available:

```python
from market_research_core.tsm_native import verify_file_bytes
# Only after separate authorization to read the complete file's opaque bytes:
receipt = verify_file_bytes(
    observed_parquet_path, expected_sha256=pinned_parquet_sha256,
    manifest_sha256=pinned_manifest_sha256,
    verification_ref=existing_verification_reference,
    allow_full_file_hash=True,
)
```

It streams SHA256, compares the expected file SHA, checks unchanged stat and
writes nothing. It never decodes OHLCV, but it reads the entire file's bytes,
possibly including Holdout bytes. It defaults to refusal and is NEVER called
implicitly by the bounded loader. No such operation was performed on a real
snapshot in this task; confirmation was requested and remains pending.

### Holdout and bounded Parquet access

Footer timestamp min/max statistics are inspected before scanning. Every
candidate row group must be entirely within the authorized research window and
disjoint from Holdout; missing/naive statistics or crossing groups are rejected.
This avoids decoding candidate groups containing Holdout, even when a timestamp
predicate would otherwise filter those rows out. Existing isolated partitions
may be needed if the actual file's row groups cross governance boundaries;
Core does not repartition or modify the source to bypass this restriction.

The Arrow scanner selects only requested dates/columns, <=4096 rows per batch,
with a hard caller row limit capped at100000. No entire-table conversion or
recursive file discovery is used. Footer metadata can describe other dates;
no Holdout OHLCV is selected/materialized. Compressed-page/internal I/O size is
not guaranteed equal to returned-row size, so this is not a byte-range projection
claim for arbitrary storage backends.

### Output schema and temporal contract

`NativeTSMResult` has `native`, `source_1m`, `conservative_15m`, `provenance`.

- `native`: requested physical columns, original names/values, Arrow-derived
  timestamp dtype/timezone. No source file or original timestamp is changed.
- `source_1m`: copied aliases `source_timestamp, open, high, low, close, volume`,
  plus symbol/contract TSM from the verified manifest identity. Timestamp values
  and original timezone are preserved in this view.
- `conservative_15m`: the EXISTING `prepare_conservative_bars(...,bar_minutes=15)`
  result. It remains label UNKNOWN, RTH-only, no closing auction/ETH, no forward
  fill, no zero-volume execution. Missing source buckets are ineligible.
  Policy `CONSERVATIVE_LABEL_AGNOSTIC v1.0.0`, SHA
  `7fa3cdcaf501651f749dbc72c31b362fcb9e10cc55920191cef87c1a050059c4`.
  Fifteen native source bars have a16-minute uncertainty envelope, not a claim
  of confirmed exchange-aligned15m boundaries. Earliest candidate open must be
  strictly later than modeled signal availability. The engine is unchanged.
- `provenance`: snapshot/producer, actual manifest/file relative paths and SHAs,
  receipt pin/reference/method, complete Arrow schema, native timestamp column
  and timezone, requested/authorized/Holdout dates, row/bucket counts, policy
  ID/version/SHA, RESEARCH_ONLY and `formal_execution_eligible=False`.
  It includes actual `core_git_sha` and `core_tree_dirty` from the checkout,
  or PEP610 VCS pin for installed wheels. An unresolvable identity is UNKNOWN,
  never an invented SHA. Claude must compare the actual identity to its install pin.

## Cost imports and exact call

```python
from market_research_core.tsm_research_costs import (
    build_tsm_research_profile, rate_record_sha256, tsm_cost_diagnostics,
)
profile = build_tsm_research_profile(
    existing_sec_cost_configuration_path,
    existing_taf_record_path,
    expected_sec_configuration_sha256=pinned_sec_configuration_sha256,
    expected_taf_sha256=pinned_taf_record_sha256,
    slippage_scenario="BASE",  # LOW1 / BASE2 / STRESS4 bps, per side
    cat_scenario="BASE",       # BASE .00005 / independent STRESS .00010 USD/share/side
)
# Execute only synthetic tests here. No real strategy run was performed.
# Existing ExecutionEngine consumes this CostProfile, unchanged.
cost_disclosure = tsm_cost_diagnostics(existing_execution_result, profile)
```

SEC input uses the existing `cost_config.load_cost_profile` JSON/YAML format,
with its dated SELL-only regulatory rate/version/evidence and pinned config SHA.
The base must express the selected Fixed assumptions: .005USD/share/side,
minimum1USD/order, maximum1% notional, zero half-spread, no conflicting fees.
No missing SEC rate is set to zero. SEC and TAF must each cover the complete
single interval `2024-06-03`–`2024-06-28`.

TAF input is an existing caller-supplied JSON rate record with exact fields:
`schema_version="1.0.0", sha256, version, effective_from, effective_to,
usd_per_share, source_ref, source_basis, verification_status`. Dates are inclusive;
null end means open-ended. Hash sorted compact JSON with only `sha256=null`.
Missing/null rate, missing source/version, unsupported classification or pin
mismatch fails. Supported evidence classifications are explicit, including
ASSUMED for synthetic records; this does not establish official FINRA rates.
No real SEC/TAF numeric rate has been provided or inferred in this task.

### TAF/CAT and exact accounting

- TAF: sourced per-share rate, **BOTH** sides, **no cap**, labeled
  `CONSERVATIVE_TAF_BOUND`. This change in side/cap treatment is a user-approved
  research assumption, not account-actual pricing.
- CAT: .00005USD/share/side BASE, .00010 independent pressure scenario. It is a
  user-approved research assumption; broker passthrough is unconfirmed and the
  number is NOT an established actual upper bound.
- Existing `CostProfile.regulatory_fee_per_unit = TAF + CAT` charges their
  combined per-share amount once on both sides. Existing SELL-only
  `sell_regulatory_fee_rate` charges SEC once. Nonzero pre-existing per-unit,
  exchange, clearing, tax or duplicate commission/slippage fields are rejected.
- The normal profile SHA binds both records, separate sources/rates, scenario
  IDs and approvals in `configuration_metadata.phase4a_cost_model`.
  Profile stays DRAFT/ASSUMED, formal cost approval false; research-assumption
  approval is recorded separately. No formal trading gate is opened.
- `tsm_cost_diagnostics` verifies result/fill profile pins and reconciles the
  actual existing Core cost calculations. It reports separate SEC, TAF and CAT
  rows/totals without debiting a second cost. The existing engine rounds SEC
  separately and TAF+CAT jointly according to the selected tax rounding mode.
  TAF attribution is pro rata at12 decimals; CAT receives the exact residual.
  Both unrounded per-share model amounts and the combined rounding delta are
  also disclosed. These allocated debits are not claims of independently billed
  or independently rounded official TAF/CAT amounts.
- `explicit_cost = commission + exchange_fee + clearing_fee + SEC + TAF + CAT`
  (normal model has zero exchange/clearing). Slippage is ALREADY in fill_price,
  gross/net PnL and equity. `price_adjustment_cost` is disclosure only, not
  another subtraction. Existing rounded charges can differ from unrounded
  assumptions; neither model label nor CAT value proves a broker-bill bound.
- No cross-rate position switching, forced liquidation or backtest stitching
  is added. Fills outside the one effective interval are rejected by the
  existing engine; open positions remain open under its existing semantics.

## Exceptions / rejection codes

`NativeSnapshotRejected(ValueError)` has stable `.code`: `UNSUPPORTED_SCOPE`,
`HOLDOUT_OR_WINDOW`, `UNBOUNDED_REQUEST`, `POLICY_SHA_MISMATCH`,
`SNAPSHOT_ID_MISMATCH`, `SNAPSHOT_SHA_MISMATCH`, `MANIFEST_LAYOUT`, `SHA_REQUIRED`,
`UNSAFE_PATH`, `INTEGRITY_RECEIPT`, `SCHEMA_MISMATCH`,
`ROW_GROUP_BOUNDARY_UNKNOWN`, `HOLDOUT_ROW_GROUP_OVERLAP`, `ROW_LIMIT`,
`EMPTY_WINDOW`, `SOURCE_CHANGED`, `FULL_HASH_NOT_AUTHORIZED`.
Malformed JSON/Arrow/OS errors can propagate as parser/Arrow/OS exceptions;
callers must record failure, not treat them as an empty successful dataset.
Cost builder/disclosure errors are ValueError; no strategy executes on refusal.
Existing execution rejections (including PIT, side/session/zero-volume/volume
cap and `COST_RATE_OUTSIDE_EFFECTIVE_DATES`) remain unchanged and are documented
in the Phase3 temporal/cost contract. Source bucket reasons and order rejection
codes remain distinct.

## Evidence, delivery pin and external blockers

See `docs/diagnostics/tsm_research_only_adapter.json` for synthetic outcomes,
source/test SHA pins, implementation commit and known blockers. The artifact's
implementation SHA pins the delivered CODE; a subsequent documentation-only
closure may have a different delivery SHA. The final response gives that delivery
SHA. `core_code_identity()` / native provenance always resolve the exact installed
commit at runtime, avoiding self-referential commit hashes inside tracked files.

Actual manifest binding, actual Parquet integrity/schema, actual SEC/TAF rates,
actual Holdout boundaries and real bounded conversion remain PENDING. No full
TSM strategy or new research phase is started. GitHub API currently returns
Forbidden, so a branch can be pushed but creating a Phase4 PR may need API access.
A prepared PR body and explicit Phase3 dependency are included with delivery.

Monetary disclosure values are Decimal; serialize them as decimal strings (for
example `json.dumps(disclosure, default=str)`), preserving precision.

## Closure evidence

Final implementation code SHA: `07c13e4820a0550be5616562615206084ef4947a`.
[GitHub CI42142215511](https://github.com/hh4832/market-research-core/actions/runs/42142215511)
passed both Python3.11 and3.12 jobs. Full local regressions each98 PASS, zero
failed/skipped. This closure only updates documentation/diagnostics; runtime
`core_code_identity()` reports the exact delivery checkout SHA.

Phase4 branch push succeeded. Actual `gh pr create` failed with
`Post https://api.github.com/graphql: Forbidden`; no Phase4 PR was created by
this session. Prepared body: `docs/PHASE4A_PR.md`. Target base is the Phase3
feature branch until dependency PR #3 merges. No source/fee evidence arrived,
so real bounded conversion remains PENDING and verdict stays SCOPED_GO.

## v1.1 development-only repair

[Development Snapshot Contract](TSM_DEVELOPMENT_SNAPSHOT_CONTRACT.md) documents the
explicit derived pin, immutable daily-row-group producer and independent receipts.
The original v1.0 reader and original Snapshot guards remain in force by default.
No actual mixed source is converted without personally signed producer permission.
