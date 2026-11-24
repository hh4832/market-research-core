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


# Fixed long-window fixtures contain only synthetic timestamps and prices.
def long_inputs(tmp_path):
    from market_research_core.tsm_development import LONG_WINDOW
    path,table,old_plan=inputs(tmp_path,True)
    times=[pd.Timestamp(t) for t in ('2016-10-18T16:00Z','2016-10-19T13:45Z',
           '2016-11-21T14:45Z','2024-12-31T23:59Z','2025-01-01T04:59Z','2025-01-01T05:00Z',
           '2027-01-01T05:00Z')]
    table=pa.table({'timestamp_utc':pa.array(times,type=pa.timestamp('us','UTC')),
                   **{k:pa.array([1000.0 if k=='volume' else 100.0]*len(times)) for k in ('open','high','low','close','volume')}})
    path.unlink();path=path.parent/'tsm_normalized.parquet'
    pq.write_table(table,path,row_group_size=len(times))
    mp=path.parent/'final_gate_manifest.json'
    mp.write_text(json.dumps({'results':[{'symbol':'TSM','published_sha256':file_sha(path)}]}))
    plan=plan_conversion([{'path':str(path),'expected_sha256':file_sha(path),'lineage_ref':'SYNTHETIC_ONLY'}],
                         parent_manifest_path=mp,expected_parent_manifest_sha256=file_sha(mp),window=LONG_WINDOW)
    return path,table,plan


def synthetic_approval(plan):
    # Fixture only: never an approval for real data or a checked-in operational approval.
    return dict(status='APPROVED',approved_by='hh4832',approval_ref='https://example.test/SYNTHETIC_ONLY',
                plan_sha256=plan['plan_sha256'],allow_holdout_decode_for_conversion_only=True,
                allow_full_source_opaque_hash=True,allow_strategy_run=False)


def test_v12_long_window_end_to_end_and_receipt_never_opens_source(tmp_path,monkeypatch):
    from market_research_core.tsm_development import LONG_WINDOW,renew_development_receipt,window_identity
    source,table,plan=long_inputs(tmp_path)
    assert plan['window_identity']==window_identity(LONG_WINDOW)
    assert plan['requires_research_lead_approval'] and plan['output_holdout_rows']==0
    bundle=produce_development_snapshot(plan,tmp_path/'out',producer_sha='a'*40,approval=synthetic_approval(plan))
    root=Path(bundle['snapshot_root']);m=json.loads((root/'manifest.json').read_text());line=json.loads((root/'lineage.json').read_text())
    assert root.name.startswith('TSM_DEV_1M_20161019_20241231_')
    assert m['contract']=='tsm-development-snapshot-v1.2'
    assert m['window_identity']==line['window_identity']==plan['window_identity']
    assert m['output_holdout_rows']==line['output_holdout_rows']==0
    assert line['sources'][0]['holdout_decode_occurred'] is True
    assert line['sources'][0]['opaque_hash_includes_holdout_bytes'] is True
    assert pq.read_table(root/m['parquet_path']).equals(table.slice(1,4))
    assert pq.ParquetFile(root/m['parquet_path']).num_row_groups==3
    report=preflight_development_snapshot(bundle)
    assert report['native_rows']==4 and len(report['verified_windows'])==3
    # Remove the entire parent: renewal and preflight must work without original mixed bytes/metadata.
    import shutil
    shutil.rmtree(source.parent)
    assert preflight_development_snapshot(renew_development_receipt(bundle))['status']=='PASS'


@pytest.mark.parametrize('window',[DateWindow('2016-10-18','2024-12-31'),DateWindow('2016-10-19','2025-01-01'),DateWindow('2024-01-01','2024-12-31')])
def test_v12_rejects_every_non_allowlisted_window(tmp_path,window):
    with pytest.raises(NativeSnapshotRejected,match='DERIVED_WINDOW'):
        plan_conversion([],parent_manifest_path=tmp_path/'absent',expected_parent_manifest_sha256='0'*64,window=window)
    with pytest.raises(NativeSnapshotRejected):
        DevelopmentSnapshotPin('TSM_DEV_1M_20161019_20241231_'+'0'*20,'a'*40,'b'*64,window.start,window.end)


def test_v12_pin_cannot_reinterpret_v11():
    with pytest.raises(NativeSnapshotRejected,match='DERIVED_ID'):
        DevelopmentSnapshotPin('TSM_DEV_1M_20161019_20241231_'+'0'*20,'a'*40,'b'*64)
    with pytest.raises(NativeSnapshotRejected,match='DERIVED_ID'):
        DevelopmentSnapshotPin('TSM_DEV_1M_20240529_20240628_'+'0'*20,'a'*40,'b'*64,'2016-10-19','2024-12-31')


def test_v12_missing_and_wrong_approval_stop_before_source_hash_decode_and_output(tmp_path,monkeypatch):
    source,_,plan=long_inputs(tmp_path)
    import market_research_core.tsm_development as dev
    real_hash=dev.file_sha
    def guarded_hash(path):
        assert Path(path)!=source,'source hash before approval'
        return real_hash(path)
    monkeypatch.setattr(dev,'file_sha',guarded_hash)
    for approval,code in [(None,'PRODUCER_APPROVAL_REQUIRED'),({**synthetic_approval(plan),'plan_sha256':'0'*64},'PRODUCER_APPROVAL_MISMATCH')]:
        with pytest.raises(NativeSnapshotRejected,match=code):
            produce_development_snapshot(plan,tmp_path/'out',producer_sha='a'*40,approval=approval)
        assert not (tmp_path/'out').exists()


def test_v12_wrong_known_source_sha_refused_before_decode(tmp_path):
    from market_research_core.tsm_development import LONG_WINDOW
    source,_,plan=long_inputs(tmp_path)
    mp=Path(plan['parent_manifest_path'])
    # A syntactically valid parent claim still must match independently hashed bytes.
    mp.write_text(json.dumps({'results':[{'symbol':'TSM','published_sha256':'0'*64}]}))
    bad=plan_conversion([{'path':str(source),'expected_sha256':'0'*64,'lineage_ref':'SYNTHETIC_ONLY'}],
                        parent_manifest_path=mp,expected_parent_manifest_sha256=file_sha(mp),window=LONG_WINDOW)
    with pytest.raises(NativeSnapshotRejected,match='SOURCE_SHA'):
        produce_development_snapshot(bad,tmp_path/'out',producer_sha='a'*40,approval=synthetic_approval(bad))


@pytest.mark.parametrize('field,value',[('window_identity',{}),('output_holdout_rows',1),('contract','tsm-development-snapshot-v1.1')])
def test_v12_manifest_identity_mismatch_refused(tmp_path,field,value):
    _,_,plan=long_inputs(tmp_path)
    b=produce_development_snapshot(plan,tmp_path/'out',producer_sha='a'*40,approval=synthetic_approval(plan))
    mp=Path(b['snapshot_root'])/'manifest.json';m=json.loads(mp.read_text());m[field]=value
    mp.write_text(json.dumps(m));b['manifest_sha256']=file_sha(mp)
    with pytest.raises(NativeSnapshotRejected,match='DERIVED_'):preflight_development_snapshot(b)


def test_archived_v11_lineage_without_v12_fields_stays_readable(tmp_path):
    from market_research_core.tsm_development import renew_development_receipt
    _,_,plan=inputs(tmp_path)
    assert plan['contract']=='tsm-producer-conversion-plan-v1' and 'window_identity' not in plan
    b=produce_development_snapshot(plan,tmp_path/'out',producer_sha='a'*40)
    root=Path(b['snapshot_root']);lp=root/'lineage.json';mp=root/'manifest.json'
    line=json.loads(lp.read_text())
    for source in line['sources']:source.pop('opaque_hash_includes_holdout_bytes',None)
    assert 'window_identity' not in line and 'output_holdout_rows' not in line
    lp.write_text(json.dumps(line));lsha=file_sha(lp)
    m=json.loads(mp.read_text());m['lineage_sha256']=lsha;mp.write_text(json.dumps(m))
    b['snapshot_pin']['lineage_sha256']=lsha;b['manifest_sha256']=file_sha(mp)
    assert preflight_development_snapshot(renew_development_receipt(b))['native_rows']==60


@pytest.mark.parametrize('field,value',[('holdout_decode_occurred',False),('opaque_hash_includes_holdout_bytes',False),('decoded_row_groups',[])])
def test_v12_lineage_cannot_hide_approved_holdout_exposure(tmp_path,field,value):
    _,_,plan=long_inputs(tmp_path)
    b=produce_development_snapshot(plan,tmp_path/'out',producer_sha='a'*40,approval=synthetic_approval(plan))
    root=Path(b['snapshot_root']);lp=root/'lineage.json';mp=root/'manifest.json'
    line=json.loads(lp.read_text());line['sources'][0][field]=value;lp.write_text(json.dumps(line))
    m=json.loads(mp.read_text());m['lineage_sha256']=file_sha(lp);mp.write_text(json.dumps(m))
    b['snapshot_pin']['lineage_sha256']=file_sha(lp);b['manifest_sha256']=file_sha(mp)
    with pytest.raises(NativeSnapshotRejected,match='DERIVED_LINEAGE'):preflight_development_snapshot(b)


# Receipt recovery is a data-only procedure; the raw conversion inputs are removed below.
def failed_long_output(tmp_path,monkeypatch,stat_field='ctime_ns',producer_sha='a'*40):
    import market_research_core.tsm_development as dev
    source,_,plan=long_inputs(tmp_path)
    approval=synthetic_approval(plan)
    monkeypatch.setattr(dev,'core_code_identity',lambda:{'core_git_sha':producer_sha,'core_tree_dirty':False})
    real_preflight=dev.preflight_development_snapshot
    def stale_drive_receipt(bundle):
        # Emulate just one FUSE stat field changing after receipt issuance, with valid byte hashes.
        bundle['integrity_receipt']['stat'][stat_field]-=1
        receipt=FileIntegrityReceipt(**bundle['integrity_receipt'])
        bundle['integrity_receipt_sha256']=receipt.sha256
        (Path(bundle['snapshot_root'])/'binding.json').write_text(json.dumps(bundle))
        return real_preflight(bundle)
    monkeypatch.setattr(dev,'preflight_development_snapshot',stale_drive_receipt)
    with pytest.raises(NativeSnapshotRejected,match='receipt does not bind unchanged file'):
        produce_development_snapshot(plan,tmp_path/'failed',producer_sha=producer_sha,approval=approval)
    monkeypatch.setattr(dev,'preflight_development_snapshot',real_preflight)
    root=next((tmp_path/'failed').iterdir())
    bundle=json.loads((root/'binding.json').read_text())
    assert (root/'FAILED.json').is_file()
    # Recovery must succeed with the original mixed Parquet and parent manifest unavailable.
    import shutil
    shutil.rmtree(source.parent)
    def forbidden(*args,**kwargs):raise AssertionError('Recovery invoked original conversion')
    for name in ('plan_conversion','produce_development_snapshot','_validate_plan'):
        monkeypatch.setattr(dev,name,forbidden)
    import market_research_core.tsm_recovery as recovery
    monkeypatch.setattr(recovery,'core_code_identity',lambda:{'core_git_sha':'b'*40,'core_tree_dirty':False})
    kwargs=dict(expected_plan_sha256=plan['plan_sha256'],expected_producer_sha=producer_sha,
                conversion_approval=approval,recovery_sha='b'*40)
    return bundle,kwargs


@pytest.mark.parametrize('field',['inode','ctime_ns','mtime_ns'])
def test_recovery_stale_drive_stat_preserves_bytes_failure_and_original_receipt(tmp_path,monkeypatch,field):
    from market_research_core.tsm_recovery import recover_development_snapshot
    b,kwargs=failed_long_output(tmp_path,monkeypatch,field)
    root=Path(b['snapshot_root']);before={p.name:file_sha(p) for p in root.iterdir()}
    with pytest.raises(NativeSnapshotRejected,match='SNAPSHOT_SHA_MISMATCH'):preflight_development_snapshot(b)
    result=recover_development_snapshot(b,tmp_path/'local',tmp_path/'archive',**kwargs)
    assert preflight_development_snapshot(result['local_binding'])['status']=='PASS'
    assert result['local_preflight']['native_rows']==4
    assert before=={p.name:file_sha(p) for p in root.iterdir()}
    archive=Path(result['archive_directory']);archived=archive/root.name
    assert before=={p.name:file_sha(p) for p in archived.iterdir()}
    record=result['recovery_provenance']
    assert record['original_stat_differences']==[field]
    assert record['original_failure_sha256']==before['FAILED.json']
    assert record['original_producer_sha']=='a'*40 and record['recovery_core_sha']=='b'*40
    assert record['conversion_approval']==kwargs['conversion_approval']
    assert record['output_holdout_rows']==0 and record['conversion_performed'] is False
    assert record['original_source_opened'] is False
    assert (archive/'RECOVERY_VERIFIED.json').is_file()
    assert record['receipt_portability']=='LOCAL_ONLY_RESTAGE_AND_RENEW_ON_NEW_MOUNT'
    # The archived local receipt is NOT falsely considered portable to Drive/new mounts.
    with pytest.raises(NativeSnapshotRejected,match='SNAPSHOT_SHA_MISMATCH'):
        preflight_development_snapshot({**result['local_binding'],'snapshot_root':str(archived)})


@pytest.mark.parametrize('artifact',['manifest.json','lineage.json','tsm_development_1m.parquet'])
def test_recovery_rejects_wrong_manifest_lineage_and_parquet_sha(tmp_path,monkeypatch,artifact):
    from market_research_core.tsm_recovery import recover_development_snapshot
    b,kwargs=failed_long_output(tmp_path,monkeypatch)
    root=Path(b['snapshot_root']);path=root/artifact
    if artifact.endswith('.parquet'):
        # Valid daily Parquet and timestamps, but wrong OHLCV bytes must fail independent SHA.
        table=pq.read_table(path)
        idx=table.schema.get_field_index('volume')
        table=table.set_column(idx,'volume',pa.array([99.0]*table.num_rows))
        days=table['timestamp_utc'].to_pandas().dt.tz_convert('America/New_York').dt.date
        with pq.ParquetWriter(path,table.schema) as writer:
            for day in sorted(set(days)):writer.write_table(table.filter(pa.array((days==day).to_numpy())))
    else:
        with path.open('a') as stream:stream.write(' ')
    with pytest.raises(NativeSnapshotRejected,match='SNAPSHOT_SHA_MISMATCH|DERIVED_LINEAGE'):
        recover_development_snapshot(b,tmp_path/'local',tmp_path/'archive',**kwargs)
    assert not (tmp_path/'archive').exists()


def test_recovery_rejects_holdout_before_parquet_copy_or_hash(tmp_path,monkeypatch):
    from market_research_core.tsm_recovery import recover_development_snapshot
    import market_research_core.tsm_development as dev
    b,kwargs=failed_long_output(tmp_path,monkeypatch)
    root=Path(b['snapshot_root']);mp=root/'manifest.json';m=json.loads(mp.read_text());data=root/m['parquet_path']
    original=pq.read_table(data);row=original.slice(0,1).to_pylist()[0]
    row['timestamp_utc']=pd.Timestamp('2025-01-01T05:00Z').to_pydatetime()
    extra=pa.Table.from_pylist([row],schema=original.schema)
    with pq.ParquetWriter(data,original.schema) as writer:
        writer.write_table(original.slice(0,1));writer.write_table(original.slice(1,1))
        writer.write_table(original.slice(2));writer.write_table(extra)
    # Re-pinning a synthetic malicious artifact must not bypass daily Holdout guards.
    m['parquet_sha256']=file_sha(data);mp.write_text(json.dumps(m));b['manifest_sha256']=file_sha(mp)
    original_hash=dev.file_sha
    def no_data_hash(path):
        assert Path(path)!=data,'Holdout body hashed before boundary rejection'
        return original_hash(path)
    monkeypatch.setattr(dev,'file_sha',no_data_hash)
    with pytest.raises(NativeSnapshotRejected,match='DERIVED_ROW_GROUP'):
        recover_development_snapshot(b,tmp_path/'local',tmp_path/'archive',**kwargs)
    assert not (tmp_path/'local').exists() and not (tmp_path/'archive').exists()


@pytest.mark.parametrize('destination',['local','archive'])
def test_recovery_never_overwrites_existing_destination(tmp_path,monkeypatch,destination):
    from market_research_core.tsm_recovery import recover_development_snapshot
    b,kwargs=failed_long_output(tmp_path,monkeypatch)
    target=tmp_path/destination;target.mkdir();(target/'sentinel').write_text('UNCHANGED')
    with pytest.raises(FileExistsError):recover_development_snapshot(b,tmp_path/'local',tmp_path/'archive',**kwargs)
    assert (target/'sentinel').read_text()=='UNCHANGED'


@pytest.mark.parametrize('mismatch',['plan','producer','approval','receipt','recovery_commit'])
def test_recovery_requires_this_snapshot_original_pins_and_evidence(tmp_path,monkeypatch,mismatch):
    from market_research_core.tsm_recovery import recover_development_snapshot
    b,kwargs=failed_long_output(tmp_path,monkeypatch,producer_sha='c'*40)
    if mismatch=='plan':kwargs['expected_plan_sha256']='0'*64
    if mismatch=='producer':kwargs['expected_producer_sha']='a'*40
    if mismatch=='approval':kwargs['conversion_approval']={**kwargs['conversion_approval'],'approval_ref':'https://example.test/OTHER_APPROVAL'}
    if mismatch=='receipt':b['integrity_receipt_sha256']='0'*64
    if mismatch=='recovery_commit':kwargs['recovery_sha']='d'*40
    with pytest.raises(NativeSnapshotRejected,match='RECOVERY_|INTEGRITY_RECEIPT'):
        recover_development_snapshot(b,tmp_path/'local',tmp_path/'archive',**kwargs)
    assert not (tmp_path/'local').exists() and not (tmp_path/'archive').exists()


def test_recovery_archive_corruption_never_gets_success_marker(tmp_path,monkeypatch):
    import market_research_core.tsm_recovery as recovery
    b,kwargs=failed_long_output(tmp_path,monkeypatch)
    original_hash=recovery.file_sha
    archive=tmp_path/'archive'
    def bad_readback(path):
        if Path(path).is_relative_to(archive) and str(path).endswith('.parquet'):return '0'*64
        return original_hash(path)
    monkeypatch.setattr(recovery,'file_sha',bad_readback)
    with pytest.raises(NativeSnapshotRejected,match='RECOVERY_ARCHIVE_SHA'):
        recovery.recover_development_snapshot(b,tmp_path/'local',archive,**kwargs)
    assert archive.exists() and not (archive/'RECOVERY_VERIFIED.json').exists()
