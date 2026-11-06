"""All data/rates here are synthetic fixtures, never real snapshot/rate evidence."""
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from market_research_core.policy import PINNED_SNAPSHOT
from market_research_core.tsm_native import (DateWindow,ManifestLayout,NativeSnapshotRejected,
                                           load_native_tsm,verify_file_bytes)
from market_research_core.tsm_research_costs import (build_tsm_research_profile,rate_record_sha256,tsm_cost_diagnostics)
from market_research_core.cost_config import configuration_sha256
from market_research_core.costs import calculate_costs
from market_research_core.temporal import CONSERVATIVE_POLICY,prepare_conservative_bars
from market_research_core.execution import ExecutionConfig,ExecutionEngine,ExecutionOrder

ROOT=Path(__file__).parents[1]


def native(start='2024-06-03 09:45',count=45):
    return pd.DataFrame(dict(NativeTime=pd.date_range(start,periods=count,freq='min',tz='America/New_York'),
                             O=100.,H=101.,L=99.,C=100.,V=100000.))


def snapshot(tmp_path,frame=None):
    root=tmp_path/PINNED_SNAPSHOT.run_name;root.mkdir()
    file=root/'actual_producer_chosen_name.parquet'
    table=pa.Table.from_pandas(native() if frame is None else frame,preserve_index=False)
    pq.write_table(table,file)
    file_sha=sha256(file.read_bytes()).hexdigest()
    schema={f.name:str(f.type) for f in pq.read_schema(file)}
    manifest=dict(identity=dict(id=PINNED_SNAPSHOT.run_name,producer=PINNED_SNAPSHOT.producer_git_sha),
                  native=dict(symbol='TSM',file=file.name,sha=file_sha,schema=schema,
                              columns=dict(timestamp='NativeTime',open='O',high='H',low='L',close='C',volume='V')))
    mp=root/'existing_manifest.json';mp.write_text(json.dumps(manifest))
    manifest_sha=sha256(mp.read_bytes()).hexdigest()
    receipt=verify_file_bytes(file,expected_sha256=file_sha,manifest_sha256=manifest_sha,
                              verification_ref='SYNTHETIC_FIXTURE_ONLY',allow_full_file_hash=True)
    layout=ManifestLayout('/identity/id','/identity/producer','/native/symbol','/native/file','/native/sha','/native/columns','/native/schema')
    args=dict(manifest_relative_path=mp.name,expected_manifest_sha256=manifest_sha,layout=layout,
              integrity_receipt=receipt,expected_receipt_sha256=receipt.sha256,symbol='TSM',
              dates=DateWindow('2024-06-03','2024-06-03'),research_window=DateWindow('2024-06-03','2024-06-28'),
              holdout=(DateWindow('2024-06-29','2024-07-31'),))
    return root,file,mp,args


def test_native_timestamp_timezone_schema_and_conversion_unchanged(tmp_path):
    root,file,manifest,args=snapshot(tmp_path)
    before={p.name:sha256(p.read_bytes()).hexdigest() for p in (file,manifest)}
    r=load_native_tsm(root,**args)
    original=pq.read_table(file).to_pandas()
    pd.testing.assert_frame_equal(r.native[original.columns],original)
    pd.testing.assert_series_equal(r.source_1m.source_timestamp,original.NativeTime,check_names=False)
    assert r.native.NativeTime.dt.tz is not None and str(r.native.NativeTime.dt.tz)=='America/New_York'
    assert list(r.conservative_15m.source_count)==[15,15,15]
    assert r.conservative_15m.iloc[0].modeled_available_at==pd.Timestamp('2024-06-03T14:00:00Z')
    assert r.provenance['scope']=='RESEARCH_ONLY' and not r.provenance['formal_execution_eligible']
    assert r.provenance['policy_sha256']==CONSERVATIVE_POLICY.sha256
    assert before=={p.name:sha256(p.read_bytes()).hexdigest() for p in (file,manifest)}


@pytest.mark.parametrize('bad_pin',['0'*64,'not-a-sha'])
def test_manifest_sha_mismatch_refuses_before_data(tmp_path,bad_pin,monkeypatch):
    root,_,_,args=snapshot(tmp_path)
    monkeypatch.setattr(pq,'ParquetFile',lambda *_:pytest.fail('data accessed before SHA check'))
    with pytest.raises(NativeSnapshotRejected):load_native_tsm(root,**{**args,'expected_manifest_sha256':bad_pin})


def test_changed_file_and_checksum_refused(tmp_path):
    root,file,_,args=snapshot(tmp_path)
    with pytest.raises(NativeSnapshotRejected,match='SNAPSHOT_SHA_MISMATCH'):
        verify_file_bytes(file,expected_sha256='0'*64,manifest_sha256=args['expected_manifest_sha256'],
                          verification_ref='synthetic',allow_full_file_hash=True)
    with pytest.raises(NativeSnapshotRejected,match='FULL_HASH_NOT_AUTHORIZED'):
        verify_file_bytes(file,expected_sha256='0'*64,manifest_sha256=args['expected_manifest_sha256'],verification_ref='x')
    with file.open('ab') as f:f.write(b'changed')
    with pytest.raises(NativeSnapshotRejected,match='SNAPSHOT_SHA_MISMATCH'):load_native_tsm(root,**args)


def test_holdout_request_rejected_before_any_path_access(tmp_path):
    _,_,_,args=snapshot(tmp_path)
    with pytest.raises(NativeSnapshotRejected,match='HOLDOUT_OR_WINDOW'):
        load_native_tsm(tmp_path/'does-not-exist',**{**args,'dates':DateWindow('2024-06-29','2024-06-29')})
    with pytest.raises(NativeSnapshotRejected,match='HOLDOUT_OR_WINDOW'):
        load_native_tsm(tmp_path/'does-not-exist',**{**args,'holdout':()})


def test_candidate_rowgroup_crossing_holdout_not_scanned(tmp_path,monkeypatch):
    frame=pd.concat([native(),native('2024-06-04 09:45')],ignore_index=True)
    root,_,_,args=snapshot(tmp_path,frame)
    monkeypatch.setattr('market_research_core.tsm_native.ds.dataset',lambda *_a,**_k:pytest.fail('unsafe group scanned'))
    with pytest.raises(NativeSnapshotRejected,match='HOLDOUT_ROW_GROUP_OVERLAP'):
        load_native_tsm(root,**{**args,'holdout':(DateWindow('2024-06-04','2024-06-04'),)})


def test_missing_and_zero_source_no_forward_fill(tmp_path):
    frame=native();frame=frame.drop(index=[3,4]);frame.loc[20,'V']=0
    root,_,_,args=snapshot(tmp_path,frame)
    r=load_native_tsm(root,**args)
    assert len(r.native)==43
    assert not r.conservative_15m.iloc[0].complete_source_bucket
    assert not r.conservative_15m.iloc[0].buy_execution_eligible
    assert r.conservative_15m.iloc[1].execution_rejection_reason=='ZERO_VOLUME_BAR'


@pytest.mark.parametrize('day,expected_open',[('2024-03-08','14:30'),('2024-03-11','13:30')])
def test_native_dst_and_rth(tmp_path,day,expected_open):
    root,_,_,args=snapshot(tmp_path,native(day+' 09:45'))
    r=load_native_tsm(root,**{**args,'dates':DateWindow(day,day),'research_window':DateWindow('2024-03-01','2024-03-31')})
    b=r.conservative_15m.iloc[0]
    assert b.session=='RTH' and b.session_open==pd.Timestamp(day+'T'+expected_open+':00Z')


def cost_records(tmp_path):
    # Synthetic rates deliberately NOT the missing actual SEC/FINRA historical record.
    doc=json.loads((ROOT/'configs/cost_profiles/ibkr_fixed_equity.json').read_text())
    doc['regulatory_schedule']=[dict(version='SYNTHETIC-SEC-ONLY',effective_from='2024-06-03',effective_to='2024-06-28',
                                    rate=.00001,basis='ASSUMED',evidence_ref='synthetic-not-official',approval_status='PENDING')]
    doc['applicability']['effective_from']='2024-06-03';doc['applicability']['effective_to']='2024-06-28'
    doc['configuration_sha256']=configuration_sha256(doc)
    sec=tmp_path/'synthetic-sec.json';sec.write_text(json.dumps(doc))
    taf=dict(schema_version='1.0.0',sha256=None,version='SYNTHETIC-TAF-ONLY',effective_from='2024-06-03',
             effective_to='2024-06-28',usd_per_share='0.0002',source_ref='synthetic-not-official',
             source_basis='ASSUMED',verification_status='ASSUMED')
    taf['sha256']=rate_record_sha256(taf)
    path=tmp_path/'synthetic-taf.json';path.write_text(json.dumps(taf))
    return sec,path,dict(expected_sec_configuration_sha256=doc['configuration_sha256'],expected_taf_sha256=taf['sha256'])


@pytest.mark.parametrize('scenario,bps',[('LOW',1),('BASE',2),('STRESS',4)])
def test_approved_research_cost_scenarios_and_fixed_limits(tmp_path,scenario,bps):
    sec,taf,args=cost_records(tmp_path)
    p=build_tsm_research_profile(sec,taf,slippage_scenario=scenario,**args)
    assert p.equity_slippage_bps==bps and p.half_spread_bps==0
    assert p.regulatory_fee_per_unit==.00025 and p.approval_status=='DRAFT'
    assert p.configuration_metadata['phase4a_cost_model']['model']=='CONSERVATIVE_TAF_BOUND'
    assert calculate_costs(100,100,'BUY',p)[0]==1
    assert calculate_costs(1,1,'BUY',p)[0]==Decimal('.01')
    stressed=build_tsm_research_profile(sec,taf,cat_scenario='STRESS',**args)
    assert stressed.regulatory_fee_per_unit==.0003
    assert stressed.configuration_metadata['phase4a_cost_model']['cat_actual_upper_bound_confirmed'] is False


def test_cost_evidence_required_and_no_double_fee_override(tmp_path):
    sec,taf,args=cost_records(tmp_path)
    with pytest.raises(ValueError,match='TAF record pin'):
        build_tsm_research_profile(sec,taf,**{**args,'expected_taf_sha256':'0'*64})
    doc=json.loads(sec.read_text());doc['profile']['exchange_fee_per_unit']=.001
    doc['profile']['component_basis']['exchange_fee_per_unit']='ASSUMED'
    doc['profile']['component_evidence']['exchange_fee_per_unit']='synthetic'
    doc['profile']['component_approval_status']['exchange_fee_per_unit']='PENDING'
    doc['configuration_sha256']=configuration_sha256(doc);sec.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='conflicting'):
        build_tsm_research_profile(sec,taf,**{**args,'expected_sec_configuration_sha256':doc['configuration_sha256']})


def test_sec_taf_cat_ledger_net_equity_reconcile_without_double_slippage(tmp_path):
    from decimal import Decimal
    sec,taf,args=cost_records(tmp_path);p=build_tsm_research_profile(sec,taf,**args)
    frame=native('2024-06-03 10:00',10).rename(columns=dict(NativeTime='source_timestamp',O='open',H='high',L='low',C='close',V='volume'))
    frame['symbol']='TSM';frame['contract']='TSM'
    b=prepare_conservative_bars(frame)
    cfg=ExecutionConfig(initial_cash=100000,bar_label='UNKNOWN',session_semantics='CONFIRMED',timestamp_timezone='America/New_York',
                        available_at_policy=CONSERVATIVE_POLICY.policy_id,temporal_policy=CONSERVATIVE_POLICY)
    def order(minute,side):
        t=pd.Timestamp('2024-06-03 10:'+minute,tz='America/New_York')
        return ExecutionOrder(symbol='TSM',market='IBKR_EQUITY',contract='TSM',signal_time=t,signal_available_at=t,
                              order_time=t,side=side,quantity=100,strategy_id='fixture',experiment_id='synthetic',time_in_force='DAY')
    result=ExecutionEngine().run(b,[order('01','BUY'),order('04','SELL')],cost_profile=p,config=cfg)
    report=tsm_cost_diagnostics(result,p);t=report['totals']
    assert len(result.ledger)==2
    assert t['sec']==Decimal('.10') and t['regulatory_total']==Decimal('.16')
    assert t['taf']==Decimal('.048') and t['cat']==Decimal('.012')
    assert t['explicit_cost']==Decimal('2.16') and t['price_adjustment_cost']==Decimal('4')
    assert result.round_trips.iloc[0].net_pnl==pytest.approx(-6.16)
    assert result.equity_curve.iloc[-1].equity==pytest.approx(99993.84)
    assert report['price_adjustment_already_in_fill_price']
    # Known uncertainty envelope: signal10:01, first candidate earliest10:02; never same-time fill.
    assert result.fills.iloc[0].exec_ts==pd.Timestamp('2024-06-03 10:02',tz='America/New_York')
