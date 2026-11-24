"""Synthetic cost/price fixtures only; not official history or a formal strategy run."""
import copy
from dataclasses import asdict, replace
from decimal import Decimal
import json
from hashlib import sha256
from pathlib import Path

import pandas as pd
import pytest

from market_research_core.dated_costs import (DatedCostSchedule, SCENARIOS, load_dated_cost_schedule,
    rate_record_sha256, schedule_sha256, new_york_fill_date)
from market_research_core.execution import ExecutionConfig, ExecutionEngine, ExecutionOrder
from market_research_core.tsm_research_costs import build_tsm_research_profile, tsm_cost_diagnostics
from test_phase4a_tsm import cost_records


def rate(kind, start, end, value, version='1'):
    r=dict(version='SYNTHETIC-'+kind+'-'+version,effective_from=start,effective_to=end,rate=value,
           unit='USD_PER_NOTIONAL' if kind=='SEC' else 'USD_PER_SHARE',
           source_ref='SYNTHETIC_FIXTURE:manually specified known-answer rate',
           source_basis='ASSUMED',verification_status='SYNTHETIC_FIXTURE',
           source_document_sha256=sha256(b'SYNTHETIC_FIXTURE: not official fee evidence').hexdigest(),record_sha256=None)
    r['record_sha256']=rate_record_sha256(r)
    return r


def sealed(doc):
    doc=copy.deepcopy(doc)
    for key in ('sec_rates','taf_rates'):
        for r in doc[key]:r['record_sha256']=rate_record_sha256(r)
    doc['schedule_sha256']=schedule_sha256(doc)
    return doc


@pytest.fixture
def document(tmp_path):
    sec,taf,pins=cost_records(tmp_path)
    base=build_tsm_research_profile(sec,taf,**pins)
    return sealed(dict(contract='tsm-dated-trading-costs-v1',schedule_id='SYNTHETIC-TSM-COSTS',version='1.0.0',
        schedule_sha256=None,scope='RESEARCH_ONLY',evidence_kind='SYNTHETIC_FIXTURE',symbol='TSM',market='IBKR_EQUITY',
        timezone='America/New_York',coverage={'start':'2024-06-03','end':'2024-06-05'},scenarios=copy.deepcopy(SCENARIOS),
        base_profile=asdict(base),base_profile_sha256=base.sha256,
        sec_rates=[rate('SEC','2024-06-03','2024-06-03','0.00001'),rate('SEC','2024-06-04','2024-06-05','0.00002','2')],
        taf_rates=[rate('TAF','2024-06-03','2024-06-04','0.0002'),rate('TAF','2024-06-05','2024-06-05','0.0003','2')]))


def schedule(doc,scenario='BASE'):
    return DatedCostSchedule.from_document(doc,expected_sha256=doc['schedule_sha256'],scenario=scenario)


def bars(days=('2024-06-03','2024-06-04','2024-06-05'),minutes=5):
    out=[]
    for day in days:
        start=pd.Timestamp(day+' 15:00',tz='America/New_York')
        for i in range(minutes):
            t=start+pd.Timedelta(minutes=i)
            out.append(dict(bar_start=t,bar_end=t+pd.Timedelta(minutes=1),available_at=t+pd.Timedelta(minutes=1),
                timezone='America/New_York',session='RTH',symbol='TSM',market='IBKR_EQUITY',contract='TSM',
                open=100.,high=101.,low=99.,close=100.,volume=100000.,data_exists=True,backtest_eligible=True,
                buy_execution_eligible=True,sell_execution_eligible=True,execution_rejection_reason='',
                session_open=pd.Timestamp(day+' 09:30',tz='America/New_York'),session_close=pd.Timestamp(day+' 16:00',tz='America/New_York')))
    return pd.DataFrame(out)


def order(day,side,quantity=100,minute=0):
    t=pd.Timestamp(day+' 15:00',tz='America/New_York')+pd.Timedelta(minutes=minute)
    return ExecutionOrder(symbol='TSM',market='IBKR_EQUITY',contract='TSM',signal_time=t-pd.Timedelta(minutes=1),
        signal_available_at=t,order_time=t,side=side,quantity=quantity,strategy_id='SYNTHETIC_SMA_5_20',
        experiment_id='SYNTHETIC_ONLY',time_in_force='DAY',order_id=day+'-'+str(minute)+'-'+side)


def config(**kw):
    d=dict(initial_cash=100000,bar_label='START',session_semantics='CONFIRMED',timestamp_timezone='America/New_York',
           same_timestamp_open_assumption=True,permitted_usage_scope='RESEARCH_ONLY',dataset_snapshot_id='SYNTHETIC_DATED_COSTS')
    d.update(kw)
    return ExecutionConfig(**d)


def run(doc,orders,frame=None,scenario='BASE',cfg=None):
    s=schedule(doc,scenario)
    return ExecutionEngine().run(bars() if frame is None else frame,orders,cost_profile=s.base_profile,
        cost_schedule=s,config=cfg or config()),s


@pytest.mark.parametrize('name',SCENARIOS)
def test_single_interval_matches_legacy_financial_results_exactly(document,tmp_path,name):
    doc=copy.deepcopy(document)
    doc['sec_rates']=[rate('SEC','2024-06-03','2024-06-05','0.00001')]
    doc['taf_rates']=[rate('TAF','2024-06-03','2024-06-05','0.0002')]
    doc=sealed(doc)
    sec,taf,pins=cost_records(tmp_path)
    legacy=build_tsm_research_profile(sec,taf,slippage_scenario='BASE' if name=='CAT_STRESS' else name,
        cat_scenario='STRESS' if name=='CAT_STRESS' else 'BASE',**pins)
    orders=[order('2024-06-03','BUY'),order('2024-06-05','SELL')]
    old=ExecutionEngine().run(bars(),orders,cost_profile=legacy,config=config())
    new,s=run(doc,orders,scenario=name)
    identity=['cost_profile_id','cost_profile_version','cost_profile_sha256']
    pd.testing.assert_frame_equal(old.fills.drop(columns=identity),new.fills[old.fills.columns].drop(columns=identity))
    for field in ('round_trips','equity_curve','open_positions','rejections','invalidated_round_trips','order_events'):
        pd.testing.assert_frame_equal(getattr(old,field),getattr(new,field))
    assert tsm_cost_diagnostics(old,legacy)['totals']==tsm_cost_diagnostics(new,s)['totals']
    assert old.status==new.status=='RESEARCH_ONLY'
    assert 'cost_schedule_sha256' not in old.provenance


def test_rates_switch_on_fill_not_order_date_and_inventory_is_not_reset(document):
    orders=[order('2024-06-03','BUY'),order('2024-06-04','SELL',40),order('2024-06-05','SELL',60)]
    result,s=run(document,orders)
    assert list(result.fills.side)==['BUY','SELL','SELL'] and list(result.fills.qty)==[100.,40.,60.]
    assert len(result.round_trips)==2 and result.open_positions.empty
    assert set(result.round_trips.entry_ts.dt.date.astype(str))=={'2024-06-03'}
    diag=tsm_cost_diagnostics(result,s)
    assert [r['sec'] for r in diag['rows']]==[Decimal(0),Decimal('.08'),Decimal('.12')]
    assert [r['taf_unrounded'] for r in diag['rows']]==[Decimal('.02'),Decimal('.008'),Decimal('.018')]
    totals=diag['totals']
    assert totals['sec']+totals['taf']+totals['cat']==totals['regulatory_total']
    assert totals['explicit_cost']==sum(result.fills.explicit_cost,Decimal(0))
    assert result.equity_curve.iloc[-1].equity==pytest.approx(100000+result.round_trips.net_pnl.sum())
    assert result.provenance['cost_state_reset'] is False
    assert result.provenance['cost_schedule_sha256']==s.sha256
    for f in result.fills.to_dict('records'):
        assert f['cost_profile_sha256']==s.profile_for(f['exec_ts']).sha256
        assert f['cost_schedule_sha256']==s.sha256
        assert f['cost_effective_date']==new_york_fill_date(f['exec_ts'])
    # A delayed order created the previous date must use the actual next day's fee.
    late=replace(order('2024-06-03','SELL'),signal_time=pd.Timestamp('2024-06-03 16:01',tz='America/New_York'),
        signal_available_at=pd.Timestamp('2024-06-03 16:01',tz='America/New_York'),
        order_time=pd.Timestamp('2024-06-03 16:01',tz='America/New_York'))
    delayed,_=run(document,[late])
    assert delayed.fills.iloc[0].cost_effective_date=='2024-06-04'
    assert tsm_cost_diagnostics(delayed,s)['totals']['sec']==Decimal('.20')


def test_new_york_date_used_even_if_engine_output_timezone_is_taipei(document):
    result,s=run(document,[order('2024-06-03','BUY'),order('2024-06-04','SELL')],
        cfg=config(timestamp_timezone='Asia/Taipei'))
    assert result.fills.iloc[0].exec_ts.date().isoformat()=='2024-06-04'
    assert list(result.fills.cost_effective_date)==['2024-06-03','2024-06-04']
    assert s.profile_for(pd.Timestamp('2024-06-04T01:00Z')).sell_regulatory_fee_rate==.00001
    with pytest.raises(ValueError,match='timezone-aware'):s.profile_for('2024-06-04')


@pytest.mark.parametrize('component',['sec_rates','taf_rates'])
@pytest.mark.parametrize('problem',['gap','overlap','missing','unknown','unverified','null_rate','bad_record_sha'])
def test_incomplete_or_untrusted_history_is_never_a_zero_fee(document,component,problem):
    doc=copy.deepcopy(document);rows=doc[component]
    if problem=='gap':
        if component=='sec_rates':rows[1]['effective_from']='2024-06-05'
        else:rows[0]['effective_to']='2024-06-03'
    elif problem=='overlap':rows[1]['effective_from']=rows[0]['effective_to']
    elif problem=='missing':doc[component]=[]
    elif problem=='unknown':rows[0]['source_basis']='UNKNOWN'
    elif problem=='unverified':rows[0]['verification_status']='UNVERIFIED'
    elif problem=='null_rate':rows[0]['rate']=None
    doc=sealed(doc)
    if problem=='bad_record_sha':
        doc[component][0]['record_sha256']='0'*64;doc['schedule_sha256']=schedule_sha256(doc)
    with pytest.raises(ValueError):schedule(doc)


def test_schedule_and_profile_sha_pins_and_immutable_snapshot(document,tmp_path):
    path=tmp_path/'costs.json';path.write_text(json.dumps(document))
    s=load_dated_cost_schedule(path,expected_sha256=document['schedule_sha256'])
    mutated=copy.deepcopy(document);mutated['taf_rates'][0]['rate']='0.1';path.write_text(json.dumps(mutated))
    with pytest.raises(ValueError,match='SHA'):load_dated_cost_schedule(path,expected_sha256=document['schedule_sha256'])
    with pytest.raises(ValueError,match='SHA'):DatedCostSchedule.from_document(document,expected_sha256='0'*64)
    mutated=sealed(mutated)
    with pytest.raises(ValueError,match='SHA'):DatedCostSchedule.from_document(mutated,expected_sha256=document['schedule_sha256'])
    changed=s.document;changed['base_profile']['minimum_commission']=100
    changed_profile=s.base_profile;changed_profile.configuration_metadata['modified']=True
    assert s.base_profile.sha256==document['base_profile_sha256']
    assert s.profile_for(pd.Timestamp('2024-06-03T15:00Z')).minimum_commission==1
    # Even re-sealed source records cannot silently select an altered/unpinned base.
    mutated=copy.deepcopy(document);mutated['base_profile']['minimum_commission']=2;mutated=sealed(mutated)
    with pytest.raises(ValueError,match='base profile SHA'):schedule(mutated)
    with pytest.raises(ValueError,match='base profile SHA'):
        ExecutionEngine().run(bars(),[],cost_profile=replace(s.base_profile,minimum_commission=2),cost_schedule=s,config=config())
    path.write_text(json.dumps(document).replace('"version": "1.0.0"','"version": "ignored", "version": "1.0.0"',1))
    with pytest.raises(ValueError,match='duplicate'):load_dated_cost_schedule(path,expected_sha256=document['schedule_sha256'])


def test_synthetic_costs_never_price_a_real_snapshot_and_no_formal_upgrade(document):
    s=schedule(document)
    with pytest.raises(ValueError,match='real dataset'):
        ExecutionEngine().run(bars(),[],cost_profile=s.base_profile,cost_schedule=s,config=config(dataset_snapshot_id='TSM_DEV_REAL'))
    with pytest.raises(ValueError,match='RESEARCH_ONLY'):
        ExecutionEngine().run(bars(),[],cost_profile=s.base_profile,cost_schedule=s,config=config(permitted_usage_scope='BACKTEST'))
    with pytest.raises(ValueError,match='coverage'):
        run(document,[order('2024-06-06','BUY')],frame=bars(('2024-06-06',)))
    with pytest.raises(ValueError,match='coverage'):
        run(document,[],cfg=config(window_start='2017-01-01T00:00:00-05:00',window_end='2025-01-01T00:00:00-05:00'))


def test_diagnostics_reject_forged_selected_profile_schedule_and_fee(document):
    result,s=run(document,[order('2024-06-03','BUY'),order('2024-06-05','SELL')])
    for field,value in [('cost_profile_sha256','0'*64),('cost_schedule_sha256','0'*64),
                        ('cost_effective_date','2024-06-04'),('regulatory_fee',Decimal(0)),
                        ('fee',Decimal(9)),('explicit_cost',Decimal(0)),('sec_rate_record_sha256','0'*64),
                        ('taf_rate_record_sha256','0'*64),('cost_schedule_version','FORGED')]:
        forged=copy.deepcopy(result);forged.fills.loc[0,field]=value
        with pytest.raises(ValueError):tsm_cost_diagnostics(forged,s)
    forged=copy.deepcopy(result);forged.provenance['cost_schedule_scenario']='LOW'
    with pytest.raises(ValueError):tsm_cost_diagnostics(forged,s)


def test_full_2017_2024_continuous_position_across_multiple_rate_changes(document):
    doc=copy.deepcopy(document);doc['coverage']={'start':'2017-01-01','end':'2024-12-31'}
    doc['sec_rates']=[rate('SEC','2017-01-01','2021-12-31','0.00001'),rate('SEC','2022-01-01','2024-12-31','0.00002','2')]
    doc['taf_rates']=[rate('TAF','2017-01-01','2022-12-31','0.0002'),rate('TAF','2023-01-01','2024-12-31','0.0003','2')]
    doc=sealed(doc);days=tuple(f'{year}-06-03' for year in range(2017,2025))
    result,s=run(doc,[order(days[0],'BUY'),order(days[-1],'SELL')],frame=bars(days),
        cfg=config(window_start='2017-01-01T00:00:00-05:00',window_end='2025-01-01T00:00:00-05:00'))
    assert len(result.fills)==2 and len(result.round_trips)==1
    assert result.fills.iloc[0].exec_ts.year==2017 and result.fills.iloc[1].exec_ts.year==2024
    assert result.equity_curve.timestamp.dt.year.nunique()==8
    assert (result.equity_curve[result.equity_curve.timestamp.dt.year.between(2018,2023)].cash==result.equity_curve.iloc[0].cash).all()
    assert result.equity_curve.iloc[-1].equity==pytest.approx(100000+result.round_trips.iloc[0].net_pnl)
    assert tsm_cost_diagnostics(result,s)['totals']['regulatory_total']==sum(result.fills.regulatory_fee,Decimal(0))


def test_same_sma_5_20_orders_keep_positions_and_cash_across_a_rate_boundary(document):
    """A test-only completed-15m crossover stream; no strategy production code is changed."""
    doc=copy.deepcopy(document)
    doc['sec_rates']=[rate('SEC','2024-06-03','2024-06-04','0.00001'),rate('SEC','2024-06-05','2024-06-05','0.00002','2')]
    doc=sealed(doc)
    frame=bars(minutes=25)
    for idx,row in frame.iterrows():
        day=row.bar_start.date().isoformat();t=pd.Timestamp(day+' 09:30',tz='America/New_York')+pd.Timedelta(minutes=15*(idx%25))
        frame.loc[idx,['bar_start','bar_end','available_at']]=[t,t+pd.Timedelta(minutes=15),t+pd.Timedelta(minutes=15)]
        price=110-idx*.2 if idx<20 else 106+(idx-20)*.2 if idx<53 else 112.6-(idx-53)*.4
        frame.loc[idx,['open','close','high','low']]=[price,price,price+.1,price-.1]
    fast=frame.close.rolling(5).mean();slow=frame.close.rolling(20).mean();orders=[]
    for i in range(20,len(frame)):
        side='BUY' if fast.iloc[i-1]<=slow.iloc[i-1] and fast.iloc[i]>slow.iloc[i] else 'SELL' if fast.iloc[i-1]>=slow.iloc[i-1] and fast.iloc[i]<slow.iloc[i] else None
        if side:
            t=frame.iloc[i].bar_end
            orders.append(ExecutionOrder(symbol='TSM',market='IBKR_EQUITY',contract='TSM',signal_time=t,
                signal_available_at=t,order_time=t,side=side,target_position=100 if side=='BUY' else 0,
                strategy_id='SYNTHETIC_SMA_5_20',experiment_id='SYNTHETIC_ONLY',time_in_force='DAY',order_id=f'sma-{i}'))
    original_orders=copy.deepcopy(orders)
    result,s=run(doc,orders,frame=frame,cfg=config(same_timestamp_open_assumption=False))
    old=ExecutionEngine().run(frame,orders,cost_profile=s.base_profile,config=config(same_timestamp_open_assumption=False))
    assert orders==original_orders and len(result.fills)==len(old.fills)==2
    assert len(result.round_trips)==len(old.round_trips)==1
    assert result.fills.iloc[0].exec_ts.date().isoformat()=='2024-06-04'
    assert result.fills.iloc[1].exec_ts.date().isoformat()=='2024-06-05'
    assert result.open_positions.empty and old.open_positions.empty
    for col in ('order_id','exec_ts','side','qty','fill_price','signal_time','signal_available_at'):
        pd.testing.assert_series_equal(result.fills[col],old.fills[col])
    pd.testing.assert_frame_equal(result.order_events,old.order_events)
    assert (result.fills.exec_ts>result.fills.signal_available_at).all()
    # Cash/equity differ ONLY by cumulative actual cost debits, never a date-boundary reset.
    for i,row in frame.iterrows():
        new_paid=sum(result.fills[result.fills.exec_ts<=row.bar_start].explicit_cost,Decimal(0))
        old_paid=sum(old.fills[old.fills.exec_ts<=row.bar_start].explicit_cost,Decimal(0))
        for col in ('cash','equity'):
            assert result.equity_curve.iloc[i][col]==pytest.approx(old.equity_curve.iloc[i][col]-float(new_paid-old_paid))
    assert tsm_cost_diagnostics(result,s)['totals']['explicit_cost']==sum(result.fills.explicit_cost,Decimal(0))


@pytest.mark.parametrize('bad_host',['example.com','www.sec.gov.evil.example','user@www.sec.gov','www.sec.gov:443'])
def test_official_evidence_requires_exact_trusted_source_domains(document,bad_host):
    doc=copy.deepcopy(document);doc['evidence_kind']='VERIFIED_SOURCE_RECORDS'
    # Adversarial metadata assertions, NOT official rate evidence or real pricing input.
    for kind in ('sec_rates','taf_rates'):
        for row in doc[kind]:
            row.update(source_basis='OFFICIAL',verification_status='VERIFIED',
                       source_ref='https://'+(bad_host if kind=='sec_rates' else 'www.finra.org')+'/rate-document')
    with pytest.raises(ValueError,match='official evidence'):schedule(sealed(doc))


def test_declared_window_and_empty_results_still_require_pinned_complete_costs(document):
    result,s=run(document,[],cfg=config(window_start='2024-06-03T00:00:00-04:00',window_end='2024-06-06T00:00:00-04:00'))
    assert result.fills.empty and tsm_cost_diagnostics(result,s)['totals']['regulatory_total']==0
    with pytest.raises(ValueError,match='both execution window'):
        run(document,[],cfg=config(window_start='2024-06-03T00:00:00-04:00'))
    with pytest.raises(ValueError,match='unsupported dated cost scenario'):schedule(document,'UNAPPROVED')


def test_real_evidence_mode_requires_actual_source_bytes_and_pins(document,tmp_path):
    # Schema/integrity test ONLY: these bytes/rates are synthetic, not an official
    # rate file, and this object is never used to price any real dataset.
    source=tmp_path/'SYNTHETIC_ONLY_source.txt';source.write_bytes(b'SYNTHETIC_ONLY_cost_source_fixture')
    digest=sha256(source.read_bytes()).hexdigest()
    doc=copy.deepcopy(document);doc['evidence_kind']='VERIFIED_SOURCE_RECORDS'
    for key in ('sec_rates','taf_rates'):
        for r in doc[key]:
            r.update(source_basis='OFFICIAL',verification_status='VERIFIED',source_document_sha256=digest,
                     source_ref='https://'+('www.sec.gov' if key=='sec_rates' else 'www.finra.org')+'/SYNTHETIC_TEST_ONLY')
    doc=sealed(doc)
    with pytest.raises(ValueError,match='source document bytes'):schedule(doc)
    s=DatedCostSchedule.from_document(doc,expected_sha256=doc['schedule_sha256'],source_documents={digest:source})
    assert s.sha256==doc['schedule_sha256']
    source.write_bytes(b'changed fixture evidence')
    with pytest.raises(ValueError,match='source document SHA mismatch'):
        DatedCostSchedule.from_document(doc,expected_sha256=doc['schedule_sha256'],source_documents={digest:source})
    # The already verified schedule is an immutable snapshot, not a live file resolver.
    assert s.profile_for(pd.Timestamp('2024-06-03T14:00Z')).sell_regulatory_fee_rate==.00001
