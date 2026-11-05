from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd
import pytest

from market_research_core.temporal import TemporalPolicy, CONSERVATIVE_POLICY, prepare_conservative_bars
from market_research_core.costs import CostProfile, calculate_fill_costs, calculate_costs, adverse_fill_price, _OLD_COMPONENTS
from market_research_core.cost_config import configuration_sha256, load_cost_profile
from market_research_core.execution import ExecutionConfig, ExecutionEngine, ExecutionOrder

ROOT = Path(__file__).parents[1]


def profile(**kw):
    d = dict(profile_id='known-answer',version='1',instrument_type='EQUITY',approval_status='DRAFT',
             source_evidence='manually computed synthetic case',basis='ASSUMED',equity_commission_rate=0,
             minimum_commission=0,sell_tax_rate=0,futures_fee_per_contract_per_side=0,futures_transaction_tax_rate=0,
             contract_multiplier=1,tick_size=.01,slippage_ticks=0,fee_rounding='NONE',tax_rounding='NONE',
             component_basis={k:'ASSUMED' for k in _OLD_COMPONENTS})
    d.update(kw)
    for k in ('equity_commission_per_share','equity_slippage_bps','half_spread_bps','sell_regulatory_fee_rate',
              'volume_participation_cap','commission_cap_rate','exchange_fee_per_unit','clearing_fee_per_unit','regulatory_fee_per_unit'):
        if d.get(k) is not None:
            d['component_basis'][k]='ASSUMED'
            d.setdefault('component_evidence',{})[k]='synthetic known answer'
            d.setdefault('component_approval_status',{})[k]='PENDING'
    return CostProfile(**d)


def source(start='2024-01-02 10:00', periods=10):
    return pd.DataFrame([dict(source_timestamp=t,symbol='TSM',contract='TSM',open=100.,high=101.,low=99.,close=100.,volume=1000.)
                         for t in pd.date_range(start,periods=periods,freq='min',tz='America/New_York')])


def config(**kw):
    d=dict(initial_cash=100000,bar_label='UNKNOWN',session_semantics='CONFIRMED',timestamp_timezone='America/New_York',
           available_at_policy='CONSERVATIVE_LABEL_AGNOSTIC',temporal_policy=CONSERVATIVE_POLICY)
    d.update(kw)
    return ExecutionConfig(**d)


def order(at,side='BUY',qty=1,**kw):
    t=pd.Timestamp(at,tz='America/New_York')
    d=dict(symbol='TSM',market='IBKR_EQUITY',contract='TSM',signal_time=t,signal_available_at=t,order_time=t,
           side=side,quantity=qty,strategy_id='known',experiment_id='M3-A.4',time_in_force='DAY')
    d.update(kw)
    return ExecutionOrder(**d)


@pytest.mark.parametrize('actual_label',['START','END'])
def test_both_known_hypotheses_have_no_lookahead(actual_label):
    raw=source(); before=raw.copy(deep=True)
    bars=prepare_conservative_bars(raw)
    result=ExecutionEngine().run(bars,[order('2024-01-02 10:01')],cost_profile=profile(),config=config())
    fill=result.fills.iloc[0]
    assert fill.exec_ts == pd.Timestamp('2024-01-02 10:02',tz='America/New_York')
    # Known-answer source used for signal is t=10:00. Completion is 10:01 (START) or 10:00 (END).
    completed=pd.Timestamp('2024-01-02 10:01' if actual_label=='START' else '2024-01-02 10:00',tz='America/New_York')
    actual_open=fill.source_timestamp - (pd.Timedelta(minutes=1) if actual_label=='END' else pd.Timedelta(0))
    assert completed <= fill.modeled_available_at < fill.exec_ts <= actual_open
    assert result.status=='RESEARCH_ONLY'
    assert result.provenance['source_timestamp_label']=='UNKNOWN'
    assert result.provenance['temporal_policy_sha256']==CONSERVATIVE_POLICY.sha256
    assert fill.temporal_policy_sha256==CONSERVATIVE_POLICY.sha256
    pd.testing.assert_frame_equal(raw,before)


def test_complete_15_source_bars_and_latest_availability():
    bars=prepare_conservative_bars(source('2024-01-02 09:45',45),bar_minutes=15)
    assert list(bars.source_count)==[15,15,15]
    assert bars.iloc[0].available_at==pd.Timestamp('2024-01-02 10:00',tz='America/New_York')
    assert bars.iloc[0].bar_start==pd.Timestamp('2024-01-02 09:44',tz='America/New_York')
    assert bars.iloc[0].volume==15000 and bars.iloc[0].close==100
    for actual_label in ('START','END'):
        result=ExecutionEngine().run(bars,[order('2024-01-02 10:00')],cost_profile=profile(),config=config())
        fill=result.fills.iloc[0]
        assert fill.exec_ts==pd.Timestamp('2024-01-02 10:14',tz='America/New_York')
        actual_open=fill.source_timestamp-(pd.Timedelta(minutes=1) if actual_label=='END' else pd.Timedelta(0))
        assert actual_open>bars.iloc[0].modeled_available_at


def test_missing_and_zero_volume_never_fabricated():
    raw=source('2024-01-02 09:45',30).drop(index=[3,4])
    bars=prepare_conservative_bars(raw,bar_minutes=15)
    assert bars.iloc[0].source_count==13 and not bars.iloc[0].signal_eligible
    assert not bars.iloc[0].buy_execution_eligible
    raw=source('2024-01-02 09:45',30);raw.loc[5,'volume']=0
    bars=prepare_conservative_bars(raw,bar_minutes=15)
    assert not bars.iloc[0].buy_execution_eligible and bars.iloc[0].execution_rejection_reason=='ZERO_VOLUME_BAR'
    r=ExecutionEngine().run(bars,[order('2024-01-02 10:00')],cost_profile=profile(),config=config())
    assert r.fills.empty and 'PIT_VIOLATION' in set(r.rejections.reason)


@pytest.mark.parametrize('start,session,eligible',[
 ('2024-01-02 08:00','OUTSIDE_SESSION',False),('2024-01-02 09:30','OUTSIDE_SESSION',False),
 ('2024-01-02 10:00','RTH',True),('2024-01-02 16:01','OUTSIDE_SESSION',False),
 ('2024-11-29 12:59','RTH',False),('2024-11-29 13:01','OUTSIDE_SESSION',False),
 ('2024-11-28 10:00','OUTSIDE_SESSION',False)])
def test_calendar_rth_eth_holiday_and_auction_exclusion(start,session,eligible):
    b=prepare_conservative_bars(source(start,1)).iloc[0]
    assert b.session==session and bool(b.buy_execution_eligible)==eligible


@pytest.mark.parametrize('day,utc_open',[('2024-03-08','14:30'),('2024-03-11','13:30')])
def test_dst_calendar_not_fixed_offset(day,utc_open):
    b=prepare_conservative_bars(source(day+' 10:00',1)).iloc[0]
    assert b.session_open==pd.Timestamp(day+'T'+utc_open+':00Z')


def test_observation_cutoff_and_tampered_timing_fail_closed():
    b=prepare_conservative_bars(source('2024-01-02 15:30',2))
    assert not b.signal_eligible.any()
    b=prepare_conservative_bars(source()); b.loc[0,'modeled_available_at']-=pd.Timedelta(minutes=1)
    with pytest.raises(ValueError,match='mismatch'):
        ExecutionEngine().run(b,[order('2024-01-02 10:01')],cost_profile=profile(),config=config())
    with pytest.raises(ValueError):config(same_timestamp_open_assumption=True)
    with pytest.raises(ValueError):config(temporal_policy=None)
    with pytest.raises(ValueError):ExecutionConfig(initial_cash=1,bar_label='UNKNOWN',session_semantics='CONFIRMED',timestamp_timezone='America/New_York')


def test_uncompleted_signal_and_limit_order_are_rejected():
    b=prepare_conservative_bars(source())
    bad=order('2024-01-02 10:01',signal_available_at=pd.Timestamp('2024-01-02 10:00',tz='America/New_York'))
    r=ExecutionEngine().run(b,[bad],cost_profile=profile(),config=config())
    assert r.fills.empty and r.rejections.iloc[0].reason=='PIT_VIOLATION'
    limit=order('2024-01-02 10:01',order_type='LIMIT',limit_price=100)
    r=ExecutionEngine().run(b,[limit],cost_profile=profile(),config=config())
    assert r.rejections.iloc[0].reason=='UNSUPPORTED_CONSERVATIVE_ORDER_TYPE'


def test_future_ohlcv_does_not_change_earlier_fill():
    raw=source(); b=prepare_conservative_bars(raw)
    first=ExecutionEngine().run(b,[order('2024-01-02 10:01')],cost_profile=profile(),config=config())
    raw.loc[9,['high','close']]=[999,998]
    second=ExecutionEngine().run(prepare_conservative_bars(raw),[order('2024-01-02 10:01')],cost_profile=profile(),config=config())
    pd.testing.assert_frame_equal(first.fills,second.fills)


def test_per_share_minimum_cap_and_fixed_vs_tiered():
    p=profile(equity_commission_per_share=.005,minimum_commission=1,commission_cap_rate=.01,commission_scheme='FIXED')
    assert calculate_costs(100,100,'BUY',p)[0]==Decimal('1')
    assert calculate_costs(100,1000,'SELL',p)[0]==Decimal('5')
    assert calculate_costs(1,1,'BUY',p)[0]==Decimal('.01')
    with pytest.raises(ValueError,match='TIERED'):replace(p,commission_scheme='TIERED')
    with pytest.raises(ValueError):replace(p,commission_scheme=None)


def test_componentized_fees_and_all_in_are_exclusive():
    p=profile(instrument_type='FUTURES',contract_multiplier=20,tick_size=.25,futures_fee_per_contract_per_side=2.5,
              fee_mode='ALL_IN')
    assert calculate_costs(100,2,'SELL',p)[0]==5
    with pytest.raises(ValueError,match='ALL_IN'):replace(p,exchange_fee_per_unit=.5)
    p=profile(instrument_type='FUTURES',contract_multiplier=20,tick_size=.25,futures_fee_per_contract_per_side=1,
              exchange_fee_per_unit=.5,clearing_fee_per_unit=.25,regulatory_fee_per_unit=.1,fee_mode='COMPONENTIZED')
    c=calculate_fill_costs(100,2,'BUY',p)
    assert c==dict(commission=Decimal('2'),exchange_fee=Decimal('1'),clearing_fee=Decimal('.5'),regulatory_fee=Decimal('.2'),transaction_tax=0)


@pytest.mark.parametrize('name,symbol,multiplier,tick_value',[('ibkr_nq','NQ',20,5),('ibkr_es','ES',50,12.5)])
def test_futures_tick_and_dynamic_txf_tax(name,symbol,multiplier,tick_value):
    p=load_cost_profile(ROOT/'configs/cost_profiles'/f'{name}.json',symbol=symbol,as_of='2025-02-01')
    assert p.contract_multiplier==multiplier and p.tick_size*p.contract_multiplier==tick_value
    assert adverse_fill_price(100,'BUY',p)==100.25 and adverse_fill_price(100,'SELL',p)==99.75
    txf=load_cost_profile(ROOT/'configs/cost_profiles/txf.json',symbol='TXF',as_of='2025-02-01')
    assert calculate_costs(20000,2,'BUY',txf)==(Decimal('100'),Decimal('160'))
    assert calculate_costs(21000,2,'SELL',txf)==(Decimal('100'),Decimal('168'))


def test_yaml_pin_and_daytrade_qualification(tmp_path):
    import yaml
    path=ROOT/'configs/cost_profiles/taiwan_equity.json'
    p=load_cost_profile(path,symbol='2330',as_of='2025-02-01')
    assert calculate_costs(100,100,'SELL',p)==(Decimal('5'),Decimal('30'))
    with pytest.raises(ValueError,match='evidence'):
        load_cost_profile(path,symbol='2330',as_of='2025-02-01',daytrade_evidence={'same_day_offset':True})
    e=dict(same_day_offset=True,instrument_eligible=True,account_eligible=True,evidence_ref='synthetic eligibility only')
    q=load_cost_profile(path,symbol='2330',as_of='2025-02-01',daytrade_evidence=e)
    assert calculate_costs(100,100,'SELL',q)[1]==Decimal('15') and q.approval_status=='DRAFT'
    doc=json.loads(path.read_text());yp=tmp_path/'profile.yaml';yp.write_text(yaml.safe_dump(doc))
    assert load_cost_profile(yp,symbol='2330',as_of='2025-02-01').sha256==p.sha256
    doc['profile']['sell_tax_rate']=.001;yp.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError,match='SHA'):load_cost_profile(yp,symbol='2330',as_of='2025-02-01')


def test_missing_dated_regulatory_rate_and_unsupported_cost_fail_closed(tmp_path):
    path=ROOT/'configs/cost_profiles/ibkr_fixed_equity.json'
    with pytest.raises(ValueError,match='missing regulatory rate'):
        load_cost_profile(path,symbol='TSM',as_of='2025-02-01')
    doc=json.loads(path.read_text());doc['regulatory_schedule'][0]['rate']=.00001
    doc['regulatory_schedule'][0]['basis']='ASSUMED';doc['configuration_sha256']=configuration_sha256(doc)
    out=tmp_path/'cost.json';out.write_text(json.dumps(doc))
    p=load_cost_profile(out,symbol='TSM',as_of='2025-02-01')
    assert p.approval_status=='DRAFT' and p.configuration_metadata['regulatory_version']=='PENDING-ACCOUNT-RATE-v1'
    assert calculate_fill_costs(100,100,'SELL',p)['regulatory_fee']==Decimal('.10')
    doc['profile']['unsupported_fee']=1;doc['configuration_sha256']=configuration_sha256(doc);out.write_text(json.dumps(doc))
    with pytest.raises(TypeError):load_cost_profile(out,symbol='TSM',as_of='2025-02-01')


def test_partial_exit_long_short_ledger_cost_attribution():
    b=prepare_conservative_bars(source())
    p=profile(equity_commission_per_share=.005,minimum_commission=1,equity_slippage_bps=2,half_spread_bps=0,
              sell_regulatory_fee_rate=.00001,volume_participation_cap=.5)
    for first,last in [('BUY','SELL'),('SELL','BUY')]:
        r=ExecutionEngine().run(b,[order('2024-01-02 10:01',first,100),order('2024-01-02 10:04',last,50)],cost_profile=p,config=config())
        assert len(r.ledger)==2 and r.round_trips.iloc[0].qty==50 and r.open_positions.iloc[0].qty==50
        for f in r.ledger.to_dict('records'):
            assert f['explicit_cost']==f['commission']+f['exchange_fee']+f['clearing_fee']+f['regulatory_fee']+f['transaction_tax']
            assert f['price_adjustment_cost']==f['slippage_cost']+f['spread_cost']+f['tick_rounding_cost']
        assert set(r.fills.cost_profile_sha256)=={p.sha256}
    blocked=ExecutionEngine().run(b,[order('2024-01-02 10:01',qty=501)],cost_profile=p,config=config())
    assert blocked.fills.empty and 'VOLUME_PARTICIPATION_EXCEEDED' in set(blocked.rejections.reason)


def test_tick_slippage_and_spread_add_without_silent_drop():
    p=profile(slippage_ticks=1,half_spread_bps=1)
    assert adverse_fill_price(100,'BUY',p)==100.02
    assert adverse_fill_price(100,'SELL',p)==99.98


def test_execution_enforces_fee_dates_and_actual_daytrade_lots():
    path=ROOT/'configs/cost_profiles/taiwan_equity.json'
    evidence=dict(same_day_offset=True,instrument_eligible=True,account_eligible=True,evidence_ref='synthetic-only')
    p=load_cost_profile(path,symbol='2330',as_of='2025-01-06',daytrade_evidence=evidence)
    rows=[]
    for day in ('2025-01-06','2025-01-07'):
        for minute in (0,1):
            start=pd.Timestamp(day+' 09:00',tz='Asia/Taipei')+pd.Timedelta(minutes=minute)
            rows.append(dict(bar_start=start,bar_end=start+pd.Timedelta(minutes=1),available_at=start+pd.Timedelta(minutes=1),
                             timezone='Asia/Taipei',session='RTH',symbol='2330',market='TW_EQUITY',contract='CASH',
                             open=100.,high=100.,low=100.,close=100.,volume=10000.,data_exists=True,backtest_eligible=True,
                             buy_execution_eligible=True,sell_execution_eligible=True,execution_rejection_reason='',
                             session_open=pd.Timestamp(day+' 09:00',tz='Asia/Taipei'),session_close=pd.Timestamp(day+' 13:30',tz='Asia/Taipei')))
    def o(day,minute,side):
        t=pd.Timestamp(day+' 09:00',tz='Asia/Taipei')+pd.Timedelta(minutes=minute)
        return ExecutionOrder(symbol='2330',market='TW_EQUITY',contract='CASH',signal_time=t-pd.Timedelta(minutes=1),
                              signal_available_at=t,order_time=t,side=side,quantity=100,strategy_id='test',experiment_id='test')
    cfg=ExecutionConfig(initial_cash=100000,bar_label='START',session_semantics='CONFIRMED',timestamp_timezone='Asia/Taipei',same_timestamp_open_assumption=True)
    same=ExecutionEngine().run(pd.DataFrame(rows),[o('2025-01-06',0,'BUY'),o('2025-01-06',1,'SELL')],cost_profile=p,config=cfg)
    # 5 bps adverse SELL fill = 99.95; 99.95 * 100 * .0015 = 14.9925 -> 14.99.
    assert same.fills.iloc[-1].fill_price==99.95
    assert same.fills.iloc[-1].tax==Decimal('14.99')
    overnight=ExecutionEngine().run(pd.DataFrame(rows),[o('2025-01-06',0,'BUY'),o('2025-01-07',0,'SELL')],cost_profile=p,config=cfg)
    assert 'DAYTRADE_TAX_NOT_QUALIFIED' in set(overnight.rejections.reason)
    assert len(overnight.fills)==1
    expired=replace(p,configuration_metadata={**p.configuration_metadata,'effective_to':'2025-01-06'})
    r=ExecutionEngine().run(pd.DataFrame(rows),[o('2025-01-07',0,'BUY')],cost_profile=expired,config=cfg)
    assert r.rejections.iloc[0].reason=='COST_RATE_OUTSIDE_EFFECTIVE_DATES'
