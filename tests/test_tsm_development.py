"""Development producer tests use synthetic rows, never actual Holdout or signed permissions."""
from dataclasses import asdict,replace
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from market_research_core.policy import PINNED_SNAPSHOT
from market_research_core.tsm_development import (plan_conversion,produce_development_snapshot,preflight_development_snapshot,
                                                file_sha,DevelopmentSnapshotPin,WINDOW,HOLDOUT)
from market_research_core.tsm_native import NativeSnapshotRejected,load_native_tsm,ManifestLayout,FileIntegrityReceipt,DateWindow


@pytest.fixture(autouse=True)
def synthetic_producer_identity(monkeypatch):
    monkeypatch.setattr("market_research_core.tsm_development.core_code_identity",lambda:{"core_git_sha":"a"*40,"core_tree_dirty":False})


def inputs(tmp_path,mixed=False):
    root=tmp_path/PINNED_SNAPSHOT.run_name;root.mkdir()
    times=list(pd.date_range('2024-05-29 13:45',periods=30,freq='min',tz='UTC'))+list(pd.date_range('2024-06-03 13:45',periods=30,freq='min',tz='UTC'))
    if mixed:times+=list(pd.date_range('2025-01-02 14:45',periods=5,freq='min',tz='UTC'))
    table=pa.table({'timestamp_utc':pa.array(times,type=pa.timestamp('us','UTC')),
                    **{k:pa.array([100.0 if k!='volume' else 1000.0]*len(times),type=pa.float64()) for k in ('open','high','low','close','volume')}})
    path=root/'isolated_2024.parquet';pq.write_table(table,path,row_group_size=len(times))
    mp=root/'final_gate_manifest.json';mp.write_text(json.dumps({'results':[{'symbol':'TSM','published_sha256':file_sha(path)}]}))
    plan=plan_conversion([{'path':str(path),'expected_sha256':file_sha(path),'lineage_ref':'SYNTHETIC_FIXTURE_ONLY'}],parent_manifest_path=mp,expected_parent_manifest_sha256=file_sha(mp))
    return path,table,plan


def test_isolated_producer_full_reader_preflight_values_types_and_daily_groups(tmp_path):
    source,original,plan=inputs(tmp_path);before=source.read_bytes()
    assert not plan['requires_research_lead_approval']
    b=produce_development_snapshot(plan,tmp_path/'outputs',producer_sha='a'*40)
    root=Path(b['snapshot_root']);manifest=json.loads((root/'manifest.json').read_text())
    actual=pq.read_table(root/manifest['parquet_path'])
    assert actual.equals(original,check_metadata=True)
    assert source.read_bytes()==before
    pf=pq.ParquetFile(root/manifest['parquet_path']);assert pf.num_row_groups==2
    assert file_sha(root/manifest['parquet_path'])==manifest['parquet_sha256']
    assert file_sha(root/'manifest.json')==b['manifest_sha256']
    report=preflight_development_snapshot(b)
    assert report['status']=='PASS' and report['native_rows']==60 and report['conservative_buckets']==4
    assert report['strategy_run'] is False and report['batch_consumption']==0
    assert set(report['checks'].values())=={'PASS'}
    assert report['provenance']['snapshot_id']==manifest['snapshot_id']
    assert report['provenance']['source_snapshot_id']==PINNED_SNAPSHOT.run_name
    assert report['provenance']['formal_execution_eligible'] is False
    with pytest.raises(FileExistsError):produce_development_snapshot(plan,tmp_path/'outputs',producer_sha='a'*40)


def test_mixed_source_stops_before_checksum_decode_or_output(tmp_path,monkeypatch):
    path,_,plan=inputs(tmp_path,True)
    assert plan['requires_research_lead_approval']
    assert plan['sources'][0]['selected_row_groups'][0]['decode_may_include_holdout']
    assert plan['sources'][0]['full_hash_byte_range']==[0,path.stat().st_size]
    assert plan['sources'][0]['selected_row_groups'][0]['compressed_column_extents']
    with pytest.raises(NativeSnapshotRejected,match='PRODUCER_APPROVAL_REQUIRED'):
        produce_development_snapshot(plan,tmp_path/'output',producer_sha='a'*40)
    assert not (tmp_path/'output').exists()
    from market_research_core.tsm_development import digest
    forged={**plan,'requires_research_lead_approval':False}
    forged['plan_sha256']=digest({k:v for k,v in forged.items() if k!='plan_sha256'})
    with pytest.raises(NativeSnapshotRejected,match='PLAN_SHA'):
        produce_development_snapshot(forged,tmp_path/'forged',producer_sha='a'*40)
    assert not (tmp_path/'forged').exists()


def test_plan_bound_synthetic_permission_conversion_excludes_holdout(tmp_path):
    _,table,plan=inputs(tmp_path,True)
    # A unit-test artifact only; not a real research-lead signature.
    approval=dict(status='APPROVED',approved_by='hh4832',approval_ref='https://example.test/SYNTHETIC_ONLY',
                  plan_sha256=plan['plan_sha256'],allow_holdout_decode_for_conversion_only=True,
                  allow_full_source_opaque_hash=True,allow_strategy_run=False)
    bad={**approval,'plan_sha256':'0'*64}
    with pytest.raises(NativeSnapshotRejected,match='PRODUCER_APPROVAL_MISMATCH'):
        produce_development_snapshot(plan,tmp_path/'bad',producer_sha='a'*40,approval=bad)
    bundle=produce_development_snapshot(plan,tmp_path/'outputs',producer_sha='a'*40,approval=approval)
    root=Path(bundle['snapshot_root']);m=json.loads((root/'manifest.json').read_text())
    out=pq.read_table(root/m['parquet_path'])
    assert out.equals(table.slice(0,60)) and out.num_rows==60
    assert json.loads((root/'lineage.json').read_text())['sources'][0]['holdout_decode_occurred']
    assert preflight_development_snapshot(bundle)['status']=='PASS'


def test_mutated_inputs_and_derived_lineage_refused(tmp_path):
    path,_,plan=inputs(tmp_path)
    changed={**plan,'output_window':{'start':'2024-01-01','end':'2024-12-31'}}
    with pytest.raises(NativeSnapshotRejected,match='PLAN_SHA'):
        produce_development_snapshot(changed,tmp_path/'output',producer_sha='a'*40)
    b=produce_development_snapshot(plan,tmp_path/'outputs',producer_sha='a'*40)
    lp=Path(b['snapshot_root'])/'lineage.json';lp.write_text('{}')
    with pytest.raises(NativeSnapshotRejected,match='DERIVED_LINEAGE'):preflight_development_snapshot(b)
    with path.open('ab') as f:f.write(b'x')
    with pytest.raises(NativeSnapshotRejected,match='SOURCE_CHANGED'):
        produce_development_snapshot(plan,tmp_path/'another',producer_sha='a'*40)


def test_old_loader_does_not_implicitly_accept_new_snapshot(tmp_path):
    _,_,plan=inputs(tmp_path);b=produce_development_snapshot(plan,tmp_path/'outputs',producer_sha='a'*40)
    with pytest.raises(NativeSnapshotRejected,match='SNAPSHOT_ID_MISMATCH'):
        load_native_tsm(b['snapshot_root'],manifest_relative_path=b['manifest_relative_path'],
                        expected_manifest_sha256=b['manifest_sha256'],layout=ManifestLayout(**b['layout']),
                        integrity_receipt=FileIntegrityReceipt(**b['integrity_receipt']),expected_receipt_sha256=b['integrity_receipt_sha256'],
                        symbol='TSM',dates=WINDOW,research_window=WINDOW,holdout=(HOLDOUT,))


def test_derived_non_candidate_holdout_group_cannot_hide_behind_manifest_claim(tmp_path):
    from market_research_core.tsm_native import verify_file_bytes
    _,_,plan=inputs(tmp_path);b=produce_development_snapshot(plan,tmp_path/'out',producer_sha='a'*40)
    root=Path(b['snapshot_root']);mp=root/'manifest.json';m=json.loads(mp.read_text());data=root/m['parquet_path']
    original=pq.read_table(data);row=original.slice(0,1).to_pylist()[0]
    row['timestamp_utc']=pd.Timestamp('2025-01-02T14:45Z').to_pydatetime()
    extra=pa.Table.from_pylist([row],schema=original.schema)
    # Deliberately forged test artifact; rehashing must not make a Holdout-containing snapshot valid.
    with pq.ParquetWriter(data,original.schema) as w:
        w.write_table(original.slice(0,30));w.write_table(original.slice(30));w.write_table(extra)
    m['parquet_sha256']=file_sha(data);mp.write_text(json.dumps(m));b['manifest_sha256']=file_sha(mp)
    r=verify_file_bytes(data,expected_sha256=m['parquet_sha256'],manifest_sha256=b['manifest_sha256'],
                        verification_ref='SYNTHETIC_FORGED_ARTIFACT_TEST',allow_full_file_hash=True)
    b['integrity_receipt']=asdict(r);b['integrity_receipt_sha256']=r.sha256
    with pytest.raises(NativeSnapshotRejected,match='DERIVED_ROW_GROUP'):preflight_development_snapshot(b)


def test_receipt_renewal_for_moved_development_data_is_independent_and_nonmutating(tmp_path):
    import shutil
    from market_research_core.tsm_development import renew_development_receipt
    _,_,plan=inputs(tmp_path);b=produce_development_snapshot(plan,tmp_path/'out',producer_sha='a'*40)
    moved=tmp_path/'new_mount'/Path(b['snapshot_root']).name;shutil.copytree(b['snapshot_root'],moved)
    moved_bundle={**b,'snapshot_root':str(moved)}
    before={p.name:file_sha(p) for p in moved.iterdir() if p.is_file()}
    with pytest.raises(NativeSnapshotRejected,match='SNAPSHOT_SHA_MISMATCH'):preflight_development_snapshot(moved_bundle)
    renewed=renew_development_receipt(moved_bundle)
    assert preflight_development_snapshot(renewed)['status']=='PASS'
    assert before=={p.name:file_sha(p) for p in moved.iterdir() if p.is_file()}
