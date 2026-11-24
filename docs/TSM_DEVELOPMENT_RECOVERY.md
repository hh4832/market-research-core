# Core v1.2: recover existing Google Drive Development snapshots

The reported diagnostics establish matching manifest, receipt-manifest,
receipt-Parquet and actual Parquet SHA256, but different receipt stat fields
(inode/mtime/ctime in one run, ctime alone in another). The immediate failure is
strict filesystem-stat binding, not an observed byte-hash mismatch. Google Drive
FUSE metadata instability is the suspected filesystem cause; it has not been
independently reproduced against the user's Drive in this workspace.

`FileIntegrityReceipt` is a byte-integrity proof plus a same-filesystem stat guard.
It is not portable across copies or mounts. The native reader still requires the
entire stat to match. No stat check was removed, no SHA mismatch becomes a warning,
and no engine, PIT/temporal, strategy, Agent, authorization or budget rule changes.

## Recover without another conversion

Use `notebooks/TSM_Development_Snapshot_Recovery.ipynb` in a fresh Colab runtime.
All human work is in its **first code cell**: Drive mounting, snapshot selection,
source/output paths, `RECOVER_EXISTING` mode, trusted binding checksum, original
plan/producer pins, original personal approval file, and explicit confirmation.
Later cells contain no `input()` and never prompt for a conversion approval.
Run once per snapshot; do not reuse one snapshot's plan or evidence for the other.
The notebook pins a clean recovery Core commit separately from the original
producer; it does not demand that old outputs acquire a new producer identity.

The API `market_research_core.tsm_recovery.recover_development_snapshot`:

1. Requires new, disjoint local and archive directories and explicit original
   plan/producer pins plus a clean pinned recovery checkout.
2. Verifies the original manifest/lineage and every daily Parquet footer before
   copying or hashing Parquet record bytes. A purported Development artifact with
   any Holdout date, spanning group, unknown boundary or wrong schema fails closed.
3. Checks the canonical original plan and this snapshot's exact original personal
   approval evidence against lineage. It never opens the paths inside that plan.
   The old receipt must still bind its complete digest, manifest and Parquet SHA;
   its stale stat is preserved in recovery provenance.
4. Copies only pinned artifact files and optional original `binding.json`,
   `FAILED.json`, `preflight.json` to `/content` local staging. It does not copy
   unrelated files or reuse Drive metadata. Snapshot ID and original bytes stay
   unchanged, including failure evidence.
5. Calls existing `renew_development_receipt()` locally. That validates all
   metadata/footers again, independently hashes the actual local Parquet against
   the original manifest SHA, and issues a local receipt. Full
   `preflight_development_snapshot()` checks receipt/stat, daily groups, Holdout
   isolation and PIT/temporal rules. No record is read from the mixed source.
6. Archives only after local PASS into a new Drive directory, reads back all
   archived snapshot/evidence bytes and verifies SHA256. It writes
   `RECOVERY_VERIFIED.json` exclusively after every archive byte check succeeds.
   An incomplete archive has no success marker and is never overwritten on retry.

Archive layout:

```text
new_archive_directory/
  TSM_DEV_1M_.../                # original artifact bytes, including FAILED.json
  recovery_input_binding.json   # binding supplied to this recovery
  recovery_binding.local.json   # renewed receipt, usable ONLY at its local path
  recovery_preflight.local.json
  recovery_provenance.json
  RECOVERY_VERIFIED.json        # SHA of provenance + PASS after archive read-back
```

Provenance records original failure/hash, old receipt/stat, observed metadata
changes, original plan/producer and approval, recovery commit, file hashes,
local receipt, local preflight and explicit no-conversion/no-source-access flags.
`FAILED.json` inside the copy remains historical failure evidence, not a new
failure of the successfully recovered local binding. Only the separate recovery
record asserts recovery success; original artifacts are never rewritten.

The archived local receipt is **not** asserted valid on Drive. To use archived
bytes later, select its `TSM_DEV_...` directory as source, stage locally and renew
again. Keep the original trusted binding and plan/evidence pins. Reading or
backtesting directly from Drive with the old receipt may still fail by design.
Do not edit a receipt stat to make it pass. Archive read-back proves bytes at
recovery time, not permanent physical WORM storage on Google Drive.

## Validation and remaining real steps

Synthetic regressions simulate each stale inode/ctime/mtime field and recover a
producer output that actually reached the failing receipt preflight. Raw synthetic
conversion sources and parent manifest are then deleted and all conversion entry
points disabled. Tests also reject corrupt Parquet/manifest/lineage, Holdout groups,
wrong plan/producer/approval/receipt/recovery pins, existing destinations and archive
read-back corruption. The original source and failed snapshot are not modified.

These are engineering tests. Actual Drive recovery must still be run by the user:
fill the first cell with one real snapshot's independently recorded binding SHA,
exact original plan/producer pins and original approval JSON; choose a new archive;
run the remaining cells; require local preflight PASS and archive marker PASS.
Repeat with the second snapshot's own evidence. Preserve both original failures.
No renewed receipt authorizes a strategy run or a research batch.

No new conversion is required if existing output passes these checks. If it fails,
stop and preserve evidence. This recovery has no fallback conversion path; any
future original mixed-source decoding requires a separate necessity finding and
fresh research-lead authorization for that exact conversion plan.
