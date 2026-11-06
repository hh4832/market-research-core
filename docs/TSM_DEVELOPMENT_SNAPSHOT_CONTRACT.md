# Development-only TSM snapshot contract v1.1

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
