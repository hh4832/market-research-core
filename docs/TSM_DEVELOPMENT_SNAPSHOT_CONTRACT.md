# Development Snapshot Contract v1.2

This is a bounded extension of v1.1, not a database rebuild or data-quality audit.
Only `tsm_development.py`, direct regression tests, this contract and the new
long-window preparation notebook change. Engine, costs, strategies, PIT policy,
formal gates, authorization/budget logs and existing snapshots are untouched.

## Allow-list and compatibility

| Identity key | Inclusive New York dates | Manifest contract | Snapshot prefix |
| --- | --- | --- | --- |
| `20240529_20240628` | 2024-05-29–2024-06-28 | `tsm-development-snapshot-v1.1` | `TSM_DEV_1M_20240529_20240628_` |
| `20161019_20241231` | 2016-10-19–2024-12-31 | `tsm-development-snapshot-v1.2` | `TSM_DEV_1M_20161019_20241231_` |

No other preparation dates are accepted. `WINDOW` and omitted pin dates retain
the v1.1 meaning. Long pins must explicitly set start/end; changing only a prefix
cannot reinterpret a v1.1 pin. Old plan canonical bytes and suffix derivation stay
compatible: suffix = first 20 hex of SHA256(canonical JSON
`{"plan": plan_sha256, "producer": producer_sha}`). JSON uses sorted keys, compact
separators and rejects NaN. Producer SHA is the full clean installed Core commit.

For v1.2, plan, manifest and lineage all carry identical `window_identity`:
key `20161019_20241231`, timezone `America/New_York`, filter_start
`2016-10-19T00:00:00`, filter_end_exclusive `2025-01-01T00:00:00`, and
holdout_start `2025-01-01`. All three declare `output_holdout_rows == 0`.
The new plan/lineage contracts are respectively
`tsm-producer-conversion-plan-v1.2` and `tsm-development-lineage-v1.2`.
Plan body digest, source identity/hash, decoded group indices, risk flags and
approval evidence are checked from pinned lineage without reopening raw sources.
The manifest pins lineage bytes; binding pins manifest, receipt and snapshot pin.

Use `plan_conversion(..., window=LONG_WINDOW)` or CLI
`plan --window 20161019_20241231`. This window requires exactly the existing Final
Gate `tsm_normalized.parquet` beside its pinned parent manifest, with the expected
source SHA equal to its TSM published claim. Production independently verifies
that claim; a wrong source SHA stops before decoding. No arbitrary sources/dates
are enabled for the new window.

## Holdout safety and acceptance evidence

| Operation | Holdout exposure | Gate / evidence |
| --- | --- | --- |
| Planning | Parent manifest and Parquet footer only; no source body hashing or record decoding | Exact canonical plan SHA, source stat/schema, compressed extents and selected groups |
| Source SHA256 | Full source bytes, including Holdout bytes | Personal exact-plan approval before hashing |
| Selected group decoding | Known original group 1 mixes Development and Holdout records | Same approval before decoding; honest `holdout_decode_occurred` retained |
| Development output | Only `[2016-10-19 00:00, 2025-01-01 00:00)` in New York | Filter, round-trip equality, all daily footer checks, zero output Holdout rows |
| Reader / runner | Only explicit development pin and safe daily groups; no raw source supplied | Existing bounded reader protections; no research authorization granted |
| Receipt renewal | Only pinned development output | Validates manifest/lineage/all daily footers before hashing; raw source never opened |

Zero output Holdout rows does **not** mean no Holdout decoding. Planning treats
all timestamps from 2025-01-01 onward as Holdout exposure, including any source
dates beyond the historical lock end. Missing approval, mismatched SHA or changed
physical plan fails closed before source hashing, decoding or output creation.
Approval is a human attestation artifact, not a cryptographic identity service;
Codex must never supply it for real conversion. Conversion permission grants no
strategy access or research authorization.

Long preflight batches only populated dates into queries spanning at most 31
calendar days. Every native call keeps its existing 100000-row limit and checks
ALL daily groups. Counts are aggregated and compared with the manifest; verified
query windows are reported. No native-reader limit or backtest behavior changes.

Synthetic CI tests cover both contracts, arbitrary-date rejection, New York
midnight exclusion (including later source dates), exact identity consistency,
wrong source hash, missing/mismatched approval, daily groups, lineage and receipt
renewal without the original source. Existing Core regressions remain required.
These tests are engineering evidence, never real Holdout/data acceptance.

## Colab delivery and stop condition

Use [long-window preparation notebook](../notebooks/TSM_Long_Development_Snapshot_Preparation.ipynb)
on GitHub, or its Open in Colab link. It clones a fixed reviewed Core commit,
installs only existing declared dependencies, mounts Drive, verifies parent
manifest/source lineage, prints/saves the exact footer-only plan and hashing /
decoding risks, then waits for the lead to personally enter evidence and
`APPROVE <exact SHA>`. Empty input raises before conversion. There is no default
approved SHA and no Agent installation or authorization proposal. It writes new
snapshot/report directories exclusively to Drive, verifies binding/manifest/
lineage/receipt/preflight, and stops. Existing v1.1 notebook is historical and
remains pinned to its original implementation; it does not prepare v1.2.

Real conversion is intentionally NOT run during development. Generating the real
long snapshot still requires Drive access, actual footer plan, personal approval
of that exact plan, and successful production/preflight. No approval is inherited
from a v1.1 plan. No Holdout backtest, metrics or OHLCV export is permitted.

---

## Historical v1.1 documentation (original scope and evidence)

### Development-only TSM snapshot contract v1.1

Continue Core PR #4 / Agent PR #5; no replacement PR, no merge or reset. The
original Snapshot, manifest, Holdout lock, SMOKE-0001 authorization/use log,
formal gates, accepted CostProfile and Phase3 evidence remain unchanged.

## Known real blocker evidence and current limitation

The real probe at Agent `4c1c70ae221c07ed65bd01b4f1127fa1f3dc4a52`
(`docs/evidence/phase4a_real_snapshot_probe_20261106_080607.json`, copied without
altering its bytes) confirms original manifest missing5 fields, candidate row
group1 spanning2021-12-14–2026-05-26 with1048576 rows, and no trusted receipt.
The original file is29363599 bytes. Its published SHA is a MANIFEST CLAIM, not
independently verified. Do not re-label that claim as cryptographic verification.

No Drive/raw chunks are mounted in this cloud workspace. Isolated2024 chunks
are **not yet inventoried on Drive**, not proven absent. No real OHLCV, Holdout,
full-file checksum or strategy is read/executed here. Real derived Snapshot and
real preflight remain PENDING. Synthetic tests prove engineering behavior only.

## 1. One metadata-only source inventory and exact conversion plan

```python
from market_research_core.tsm_development import (
    inventory_isolated_sources, plan_conversion, produce_development_snapshot,
    preflight_development_snapshot, DevelopmentSnapshotPin,
)
inventory = inventory_isolated_sources(
    caller_declared_TSM_upstream_roots,
    parent_manifest_path=actual_original_manifest,
    parent_manifest_sha256=observed_parent_manifest_sha,
)
# Source objects: path, expected_sha256 (actual known value or explicit None), lineage_ref.
plan = plan_conversion(
    explicitly_selected_sources,
    parent_manifest_path=actual_original_manifest,
    expected_parent_manifest_sha256=observed_parent_manifest_sha,
)
```

Inventory searches ONLY explicit caller-declared TSM upstream roots, <=1000
Parquet files. It reads footers and parent metadata, never OHLCV or full source
checksums. Empty roots mean NOT_PROVIDED, not 'no chunks exist'. Truncated
inventory is incomplete. A source must have the native six columns and a
zone-aware timestamp. This is not a CSV/IBKR normalization pipeline: incompatible
raw formats fail, and source identity must come from the caller's existing TSM
producer records. Independent source hashes are computed during permitted
production; no equivalence to all original normalized prices is claimed.

Plan records every explicit path, source stat/schema, selected row groups,
physical compressed column offsets/sizes, full opaque-hash byte interval and
whether hash bytes or decoded groups include Holdout. It pins parent manifest
bytes, expected source hashes when available, dates, exact actions and a canonical
plan SHA. Sources without any intersecting row group are rejected.

Preferred path: isolated native2024 chunks/research sources whose complete
source file and selected groups do not touch Holdout. This is authorized by the
task; no extra producer permission is required. Overlapping/duplicate source
rows are rejected rather than silently deduplicated. Actual raw identity/lineage
references must be supplied; filename/schema alone is not proof of TSM identity.

Fallback: original normalized group1 is MIXED. Hashing covers `[0,29363599)`
including Holdout bytes; decoding group1's six native columns may materialize
Holdout OHLCV before filtering. It is FALSE to describe that operation as a
Holdout-free projection. Exact compressed extents come from the actual footer,
not this document. The code refuses before source hashing, decoding or creating
output unless a research-lead approval binds the actual plan SHA.

`docs/proposals/TSM_PRODUCER_CONVERSION_UNSIGNED.json` is an unsigned preview,
not permission. A real approval must have status APPROVED, approved_by hh4832,
a real https approval_ref, exact plan_sha256, both explicit hash/decode permissions
true, and allow_strategy_run false. The research lead must personally sign and
review that artifact. Codex does not create an approved record or call an admin
signing tool. Producer permission does not grant research authorization.

## 2. Immutable producer and independent proof

```python
bundle = produce_development_snapshot(
    plan, separate_output_parent,
    producer_sha=actual_clean_reviewed_core_sha,
    approval=personally_signed_producer_approval_or_None,
)
report = preflight_development_snapshot(bundle)
```

Producer SHA must equal the installed clean Core code (or immutable wheel VCS
pin). The producer never writes to source directories. Output ID is
`TSM_DEV_1M_20240529_20240628_<20hex plan+producer digest>`; existing directories
are refused. A failed run leaves a FAILED.json in its newly allocated directory,
never a reusable success artifact. Source stat guards apply before/after checksum
and decoding; a checksum mismatch stops before decoding.

Only selected source groups are decoded. Filtering is inclusive New York dates
2024-05-29–2024-06-28. Output is sorted, never filled, price-adjusted, deduplicated,
resampled or supplemented with synthetic bars. The native Arrow values, timezone,
unit and schema survive a round-trip equality check. One NY date per Parquet row
group. No OHLCV from2025–2026 is written. Missing bars remain missing.

New artifact files:

- `tsm_development_1m.parquet`: native six columns, daily row groups.
- `manifest.json`: all7 required native fields, schema, immutable ID, full new
  producer SHA, window, scope, row-group policy and lineage pin.
- `lineage.json`: original Snapshot/full producer SHA, actual parent manifest
  hash, each input's independently computed hash, matched-declared-hash status,
  source refs and decoded groups; records honestly if approved conversion decoded
  Holdout. Original published hash remains a claim unless independently matched.
- `binding.json`: pinned manifest, layout, typed DevelopmentSnapshotPin and
  independent FileIntegrityReceipt/hash. The NEW output file is hashed twice
  (manifest pin, independent receipt verification); it contains no Holdout, so
  that proof does not read the original held-out groups.
- `preflight.json`: data-only actual native load,15m conservative conversion,
  recomputed temporal/PIT invariants, counts and reader provenance. No signal,
  ExecutionEngine, SMA, metrics, reservation or research authorization is run.

## 3. Reader v1.1 is explicit, no bypass

`load_native_tsm(..., snapshot_pin=DevelopmentSnapshotPin(...))` is the only
new optional reader argument. The pin binds exact derived ID, producer SHA,
lineage SHA and fixed development dates. The default reader still accepts ONLY
the original pinned Snapshot and still rejects its mixed groups.

Derived mode verifies the versioned contract, original parent lineage, no-Holdout
output declaration, exact new identity, all existing manifest/file/receipt/schema
checks and actual daily group boundaries. It still enforces requested dates,
Holdout, row limit, policy SHA and source-change guards. No guard is skipped.
A valid caller pin is not research-lead authorization: Agent RA04 independently
rejects the old signed authorization for the new identity.

## 4. Agent unsigned proposal and no-consumption preflight

```python
from strategy_agent.research_only.development_snapshot import prepare_development_proposal
artifacts = prepare_development_proposal(
    bundle, core_sha=actual_reader_core_sha, output_directory=separate_report_dir,
)
```

The function checks real derived data through Core, creates a new config and
UNSIGNED authorization proposal, and writes them exclusively outside formal logs.
It preserves original dataset family/Holdout lock but pins the new Snapshot/full
producer SHA, manifest/receipt/lineage, reviewed Core reader, fixed SMA5/20,
existing costs and formal gates. New authorization ID differs from SMOKE-0001.
It does not reuse the old budget or pretend to be hh4832. It must not be passed
to `run` before personal signoff. The checked-in DEV-TEMPLATE is intentionally
incomplete and invalid for approval until a real bundle exists.

Existing Agent probe/execution binding now passes the explicit derived pin.
Receipt SHA binds all receipt fields, manifest and file before consumption.
An unsigned config reports RA01 instead of crashing; a new binding paired with
an old dataset identity reports RO05. Using the old authorization with the new
config reports RA04. No consuming API is called in this task or notebook.
After real data PASS AND research lead signs the new proposal, the existing
non-consuming probe can establish eligibility for the next separately invoked
research batch. Formal market eligibility remains NO-GO.

## 5. Colab and stop condition

`notebooks/TSM_Development_Snapshot_Preparation.ipynb` installs pinned Core/Agent,
mounts the existing Drive, inventories explicit upstream roots, writes the exact
plan/unsigned approval template, and produces only safe inputs or signed-plan
conversion. Missing mixed permission yields PENDING; no decode occurs. It then
runs the new data preflight and creates the unsigned research proposal. Formal
files and old uses are fingerprinted before/after. No actual notebook/Drive run
has occurred in this cloud session.

This is one bounded producer preparation, not a new full-history validation
phase. Only two human approvals can remain: conditional mixed producer conversion
if isolated source is unavailable, and the new research authorization after its
actual Snapshot pins exist. Neither is signed or consumed by Codex.

Receipt portability: after copying an existing derived Snapshot to another
machine, call `renew_development_receipt(bundle)` and save its returned binding
outside the immutable Snapshot. It verifies the pinned manifest/lineage/schema
and ALL daily footer boundaries before hashing the development-only file. It
never opens the parent source, never rewrites the Snapshot, and then requires the
normal preflight again. This is not permission to hash the original mixed file.

Reviewed producer/reader implementation pin:
`b16179a7cc077c36046574769a32c367fa476cae`; Agent proposal/probe pin:
`9e368f8925a425f7dae8921291c168b5d197f33a`. The cleared-output notebook installs
these immutable revisions, not a moving branch. Later notebook/evidence commits
do not change those installed implementations. Local Core regression:106 PASS
on each Python3.11/3.12 (including7 synthetic producer tests and notebook static
safety/syntax validation). Synthetic proof is not actual Drive preflight.
