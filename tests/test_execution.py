from dataclasses import replace

import pandas as pd
import pytest
from market_research_core.costs import CostProfile, calculate_costs
from market_research_core.execution import ExecutionConfig, ExecutionEngine, ExecutionOrder
FIELDS=("equity_commission_rate","minimum_commission","sell_tax_rate","futures_fee_per_contract_per_side","futures_transaction_tax_rate","contract_multiplier","tick_size","slippage_ticks","fee_rounding","tax_rounding")
def profile(approved=True):
 return CostProfile(profile_id="test",version="1",instrument_type="EQUITY",approval_status="APPROVED" if approved else "DRAFT",source_evidence="fixture",basis="ASSUMED",equity_commission_rate=.001,minimum_commission=1,sell_tax_rate=.003,futures_fee_per_contract_per_side=10,futures_transaction_tax_rate=.00002,contract_multiplier=50,tick_size=1,slippage_ticks=0,fee_rounding="HALF_UP",tax_rounding="HALF_UP",component_basis={x:"ASSUMED" for x in FIELDS})
def config(**kw):
 d=dict(initial_cash=100000,bar_label="START",session_semantics="CONFIRMED",timestamp_timezone="Asia/Taipei",same_timestamp_open_assumption=True,core_commit_sha="a"*40,dataset_snapshot_id="snap",dataset_producer_sha="b"*40,dataset_verdict="GO",permitted_usage_scope="BACKTEST"); d.update(kw); return ExecutionConfig(**d)
def bars():
 out=[]
 for tm,p,v in [("09:00",100,100),("09:01",101,100),("09:02",102,100),("09:03",101,100)]:
  s=pd.Timestamp("2024-01-02 "+tm,tz="Asia/Taipei")
  out.append(dict(bar_start=s,bar_end=s+pd.Timedelta(minutes=1),available_at=s+pd.Timedelta(minutes=1),timezone="Asia/Taipei",session="RTH",symbol="2330",market="TW_EQUITY",contract="CASH",open=p,high=p+1,low=p-1,close=p,volume=v,data_exists=True,backtest_eligible=True,buy_execution_eligible=True,sell_execution_eligible=True,execution_rejection_reason="",session_open=pd.Timestamp("2024-01-02 09:00",tz="Asia/Taipei"),session_close=pd.Timestamp("2024-01-02 13:30",tz="Asia/Taipei")))
 return pd.DataFrame(out)
def order(tm,side,qty=1,tif="NEXT_BAR"):
 return ExecutionOrder(symbol="2330",market="TW_EQUITY",contract="CASH",signal_time=pd.Timestamp("2024-01-02 08:59",tz="Asia/Taipei"),signal_available_at=pd.Timestamp("2024-01-02 09:00",tz="Asia/Taipei"),order_time=pd.Timestamp(tm,tz="Asia/Taipei"),side=side,quantity=qty,strategy_id="s",experiment_id="e",time_in_force=tif)
def test_pit_and_same_timestamp():
 r=ExecutionEngine().run(bars(),[order("2024-01-02 09:00","BUY")],cost_profile=profile(),config=config())
 assert r.fills.iloc[0].execution_assumption=="SAME_TIMESTAMP_BAR_OPEN"
 assert r.provenance["execution_convention"]=="oracle-execution-convention-v1"
 with pytest.raises(ValueError,match="UNKNOWN"): config(bar_label="UNKNOWN")
def test_pit_rejected():
 o=order("2024-01-02 09:00","BUY"); o=ExecutionOrder(**{**o.__dict__,"order_time":pd.Timestamp("2024-01-02 08:59",tz="Asia/Taipei")})
 r=ExecutionEngine().run(bars(),[o],cost_profile=profile(),config=config()); assert r.rejections.iloc[0].reason=="PIT_VIOLATION"
def test_side_eligibility_and_day_tif():
 f=bars(); f.loc[0,"buy_execution_eligible"]=False; f.loc[0,"execution_rejection_reason"]="LIMIT_UP_LOCKED_BUY"
 r=ExecutionEngine().run(f,[order("2024-01-02 09:00","BUY",tif="DAY")],cost_profile=profile(),config=config())
 assert r.fills.iloc[0].exec_ts==pd.Timestamp("2024-01-02 09:01",tz="Asia/Taipei")
def test_zero_volume_rejected():
 f=bars(); f.loc[0,"volume"]=0
 r=ExecutionEngine().run(f,[order("2024-01-02 09:00","BUY")],cost_profile=profile(),config=config()); assert r.rejections.iloc[0].reason=="ZERO_VOLUME_BAR"
def test_sell_tax_only():
 assert calculate_costs(100,10,"BUY",profile())[1]==0 and calculate_costs(100,10,"SELL",profile())[1]==3
def test_fifo_and_open_position():
 r=ExecutionEngine().run(bars(),[order("2024-01-02 09:00","BUY",2),order("2024-01-02 09:02","SELL")],cost_profile=profile(False),config=config(permitted_usage_scope="RESEARCH_ONLY"))
 assert len(r.round_trips)==1 and len(r.open_positions)==1 and r.status=="RESEARCH_ONLY"
def test_exclusion_invalidates_crossing_trade():
 r=ExecutionEngine().run(bars(),[order("2024-01-02 09:00","BUY"),order("2024-01-02 09:03","SELL")],cost_profile=profile(),config=config(exclusion_windows=(("2024-01-02T09:02:00+08:00","2024-01-02T09:03:00+08:00","posthoc"),)))
 assert len(r.invalidated_round_trips)==1 and r.round_trips.empty

def test_unknown_execution_timing_fails_closed():
 f=bars(); f.loc[0,"bar_start"]=pd.NaT
 with pytest.raises(ValueError,match="UNKNOWN"):
  ExecutionEngine().run(f,[order("2024-01-02 09:00","BUY")],cost_profile=profile(),config=config())

def test_cost_multiplier_scales_cost_and_slippage():
 from dataclasses import replace
 p=replace(profile(),slippage_ticks=1)
 r=ExecutionEngine().run(bars(),[order("2024-01-02 09:00","BUY")],cost_profile=p,config=config(cost_multiplier=2))
 assert r.fills.iloc[0].fill_price==102
 assert r.fills.iloc[0].fee==2
 assert r.provenance["cost_multiplier"]==2

def test_futures_use_margin_not_full_notional_and_reject_margin_breach():
 f=bars(); f["symbol"]="TXF"; f["market"]="TXF"; f["contract"]="TXF202403"
 o=ExecutionOrder(symbol="TXF",market="TXF",contract="TXF202403",
   signal_time=pd.Timestamp("2024-01-02 08:59",tz="Asia/Taipei"),
   signal_available_at=pd.Timestamp("2024-01-02 09:00",tz="Asia/Taipei"),
   order_time=pd.Timestamp("2024-01-02 09:00",tz="Asia/Taipei"),side="BUY",
   quantity=1,strategy_id="s",experiment_id="e")
 p=CostProfile(profile_id="future",version="1",instrument_type="FUTURES",approval_status="DRAFT",
   source_evidence="fixture",basis="ASSUMED",equity_commission_rate=0,minimum_commission=0,
   sell_tax_rate=0,futures_fee_per_contract_per_side=10,futures_transaction_tax_rate=.00002,
   contract_multiplier=50,tick_size=1,slippage_ticks=0,fee_rounding="HALF_UP",tax_rounding="HALF_UP",
   component_basis={x:"ASSUMED" for x in FIELDS})
 ok=ExecutionEngine().run(f,[o],cost_profile=p,config=config(initial_margin_per_contract=1000,permitted_usage_scope="RESEARCH_ONLY"))
 assert ok.equity_curve.iloc[-1].margin_requirement==1000
 assert ok.equity_curve.iloc[-1].cash>99000
 blocked=ExecutionEngine().run(f,[o],cost_profile=p,config=config(initial_cash=1000,initial_margin_per_contract=2000,permitted_usage_scope="RESEARCH_ONLY"))
 assert blocked.rejections.iloc[0].reason=="INSUFFICIENT_MARGIN"
 assert blocked.fills.empty

def test_outside_session_order_is_rejected():
 f=bars(); f.loc[0,"session_open"]=pd.Timestamp("2024-01-02 09:01",tz="Asia/Taipei")
 r=ExecutionEngine().run(f,[order("2024-01-02 09:00","BUY")],cost_profile=profile(),config=config())
 assert r.rejections.iloc[0].reason=="OUTSIDE_SESSION"

def test_formal_status_requires_complete_approved_provenance():
 from dataclasses import replace
 p=replace(profile(),component_basis={x:"OFFICIAL" for x in FIELDS})
 r=ExecutionEngine().run(bars(),[order("2024-01-02 09:00","BUY")],cost_profile=p,
   config=config(spec_sha256="c"*64,dataset_verdict="SCOPED_GO"))
 assert r.status=="FORMAL_ELIGIBLE"

def test_formal_status_rejects_short_producer_sha():
 from dataclasses import replace
 p=replace(profile(),component_basis={x:"OFFICIAL" for x in FIELDS})
 r=ExecutionEngine().run(bars(),[order("2024-01-02 09:00","BUY")],cost_profile=p,
   config=config(spec_sha256="c"*64,dataset_producer_sha="20eba73",dataset_verdict="SCOPED_GO"))
 assert r.status=="RESEARCH_ONLY"

def test_fills_and_position_ledger_follow_execution_time_not_order_creation_time():
 f=bars()
 f.loc[[0,1],"buy_execution_eligible"]=False
 f.loc[[0,1],"buy_execution_rejection_reason"]="LIMIT_UP_LOCKED_BUY"
 delayed_buy=order("2024-01-02 09:00","BUY",tif="DAY")
 earlier_fill_sell=replace(order("2024-01-02 09:01","SELL"),
   signal_available_at=pd.Timestamp("2024-01-02 09:01",tz="Asia/Taipei"),
   order_time=pd.Timestamp("2024-01-02 09:01",tz="Asia/Taipei"))
 r=ExecutionEngine().run(f,[delayed_buy,earlier_fill_sell],cost_profile=profile(),
   config(permitted_usage_scope="RESEARCH_ONLY"))
 fill_times=list(r.fills.exec_ts)
 assert fill_times==sorted(fill_times)
 assert all(pd.Timestamp(trip.entry_ts)<=pd.Timestamp(trip.exit_ts) for trip in r.round_trips.itertuples())
 assert r.round_trips.iloc[0].side=="SHORT"
