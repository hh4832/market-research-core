"""Recover immutable development outputs on local storage; never reopen conversion sources."""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

from .tsm_development import (
    DevelopmentSnapshotPin, _development_file, _validate_approval, digest, file_sha,
    preflight_development_snapshot, refuse, renew_development_receipt,
)
from .tsm_native import FileIntegrityReceipt, _inside, _stamp, core_code_identity


def _save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def recover_development_snapshot(bundle: dict, local_directory: str | Path,
                                 archive_directory: str | Path, *,
                                 expected_plan_sha256: str, expected_producer_sha: str,
                                 conversion_approval: dict | None, recovery_sha: str) -> dict:
    """Stage existing output locally, renew its receipt, preflight, then archive new bytes.

    Caller pins the original binding/manifest and supplies that snapshot's ORIGINAL
    conversion evidence. No new conversion approval is minted. Original stat fields
    are recorded, not reused as proof on a different filesystem. Native reader,
    byte SHA, daily boundary, lineage, PIT and receipt checks remain unchanged.

    Both destination directories must be new and disjoint from the input and each
    other. Local storage must have stable metadata (in Colab use /content, NOT Drive).
    Archive retains original files verbatim, including binding.json and FAILED.json.
    The renewed binding is a separate local-only artifact; on another mount stage
    and renew again. RECOVERY_VERIFIED.json is written only after archive read-back.
    """
    pin=DevelopmentSnapshotPin(**bundle['snapshot_pin'])
    if (not re.fullmatch('[0-9a-f]{64}',expected_plan_sha256)
            or expected_producer_sha!=pin.producer_sha):
        refuse('RECOVERY_PIN','explicit original plan and producer pins required')
    identity=core_code_identity()
    if (not re.fullmatch('[0-9a-f]{40}',recovery_sha)
            or identity['core_git_sha']!=recovery_sha or identity['core_tree_dirty'] is not False):
        refuse('RECOVERY_CODE','recovery must use the pinned clean Core checkout')
    source=Path(bundle['snapshot_root']).resolve(strict=True)
    local=Path(local_directory).resolve()
    archive=Path(archive_directory).resolve()
    for destination in (local,archive):
        if destination.exists():raise FileExistsError(destination)
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            refuse('RECOVERY_PATH','destinations must be outside the original snapshot')
    if local.is_relative_to(archive) or archive.is_relative_to(local):
        refuse('RECOVERY_PATH','local staging and archive must be disjoint')

    # Reads only pinned manifest/lineage and Parquet footer. NO original source paths opened.
    doc,data=_development_file(bundle)
    lineage=json.loads(_inside(source,doc['lineage_path']).read_text())
    plan=lineage.get('producer_plan',{})
    if (lineage.get('plan_sha256')!=expected_plan_sha256
            or plan.get('plan_sha256')!=expected_plan_sha256
            or digest({k:v for k,v in plan.items() if k!='plan_sha256'})!=expected_plan_sha256
            or plan.get('output_window')!=asdict(pin.window)):
        refuse('RECOVERY_PLAN','original plan digest/window differs from explicit recovery pin')
    _validate_approval(plan,conversion_approval)
    if digest(conversion_approval)!=digest(lineage.get('conversion_approval')):
        refuse('RECOVERY_APPROVAL','evidence differs from this snapshot original conversion approval')
    original_receipt=FileIntegrityReceipt(**bundle['integrity_receipt'])
    if (original_receipt.sha256!=bundle['integrity_receipt_sha256']
            or original_receipt.manifest_sha256!=bundle['manifest_sha256']
            or original_receipt.parquet_sha256!=doc['parquet_sha256']
            or not original_receipt.verification_ref):
        refuse('INTEGRITY_RECEIPT','original receipt must still bind pinned manifest and Parquet hashes')
    observed_stat=_stamp(data)

    # Whitelist pinned snapshot artifacts. Never recursively discover/copy unrelated Parquet.
    names={bundle['manifest_relative_path'],doc['lineage_path'],doc['parquet_path']}
    for name in ('binding.json','FAILED.json','preflight.json'):
        if (source/name).exists():names.add(name)
    files={name:_inside(source,name) for name in names}
    local.mkdir(parents=True,exist_ok=False)
    staged=local/pin.snapshot_id
    staged.mkdir(exist_ok=False)
    for name,path in files.items():
        target=staged/name
        target.parent.mkdir(parents=True,exist_ok=True)
        # Do not propagate Drive inode/time metadata via copy2/copystat.
        with path.open('rb') as reader, target.open('xb') as writer:
            shutil.copyfileobj(reader,writer)
    staged_bundle=copy.deepcopy(bundle)
    staged_bundle['snapshot_root']=str(staged)
    # Independently hash actual local Parquet against original manifest, then full reader preflight.
    renewed=renew_development_receipt(staged_bundle)
    report=preflight_development_snapshot(renewed)
    _save(local/'recovery_binding.local.json',renewed)
    _save(local/'recovery_preflight.local.json',report)
    _save(local/'recovery_input_binding.json',bundle)
    hashes={name:file_sha(staged/name) for name in sorted(names)}
    if hashes[bundle['manifest_relative_path']]!=bundle['manifest_sha256']:
        refuse('SNAPSHOT_SHA_MISMATCH','staged manifest changed during recovery')
    if hashes[doc['parquet_path']]!=doc['parquet_sha256']:
        refuse('SNAPSHOT_SHA_MISMATCH','staged Parquet changed during recovery')
    if hashes[doc['lineage_path']]!=pin.lineage_sha256:
        refuse('DERIVED_LINEAGE','staged lineage changed during recovery')
    failure=json.loads((staged/'FAILED.json').read_text()) if 'FAILED.json' in names else None
    record=dict(contract='tsm-development-recovery-v1',mode='RECOVER_EXISTING',
                recovered_at=datetime.now(timezone.utc).isoformat(),snapshot_id=pin.snapshot_id,
                original_snapshot_root=str(source),local_snapshot_root=str(staged),
                archive_snapshot_root=str(archive/pin.snapshot_id),
                original_producer_sha=pin.producer_sha,original_plan_sha256=expected_plan_sha256,
                recovery_core_sha=recovery_sha,conversion_approval=conversion_approval,
                original_binding_sha256=digest(bundle),original_receipt_sha256=original_receipt.sha256,
                original_receipt_stat=original_receipt.stat,observed_source_stat=observed_stat,
                original_stat_differences=[k for k,v in original_receipt.stat.items() if observed_stat.get(k)!=v],
                original_failure=failure,original_failure_sha256=hashes.get('FAILED.json'),
                local_receipt_sha256=renewed['integrity_receipt_sha256'],snapshot_files_sha256=hashes,
                conversion_performed=False,original_source_opened=False,output_holdout_rows=0,
                preflight_status=report['status'],receipt_portability='LOCAL_ONLY_RESTAGE_AND_RENEW_ON_NEW_MOUNT',
                strategy_run=False,batch_consumption=0,research_authorization='NOT_CREATED')
    _save(local/'recovery_provenance.json',record)

    # Publish only after local PASS; keep an incomplete archive on any later failure.
    archive.mkdir(parents=True,exist_ok=False)
    shutil.copytree(staged,archive/pin.snapshot_id)
    for name in ('recovery_binding.local.json','recovery_preflight.local.json',
                 'recovery_input_binding.json','recovery_provenance.json'):
        with (local/name).open('rb') as reader, (archive/name).open('xb') as writer:
            shutil.copyfileobj(reader,writer)
    # Archive metadata is intentionally not a trusted receipt. Verify its actual bytes instead.
    for name,expected in hashes.items():
        if file_sha(archive/pin.snapshot_id/name)!=expected:
            refuse('RECOVERY_ARCHIVE_SHA','archive read-back differs from verified local snapshot')
    for name in ('recovery_binding.local.json','recovery_preflight.local.json',
                 'recovery_input_binding.json','recovery_provenance.json'):
        if file_sha(archive/name)!=file_sha(local/name):
            refuse('RECOVERY_ARCHIVE_SHA','archive recovery evidence differs from local evidence')
    _save(archive/'RECOVERY_VERIFIED.json',dict(status='PASS',recovery_provenance_sha256=file_sha(local/'recovery_provenance.json'),
          archive_snapshot_root=str(archive/pin.snapshot_id),receipt_portability=record['receipt_portability']))
    return dict(local_binding=renewed,local_preflight=report,archive_directory=str(archive),recovery_provenance=record)
