from dataclasses import replace
from decimal import Decimal

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
 assert r.rejections.iloc[0].bar_ts==pd.Timestamp("2024-01-02 09:00",tz="Asia/Taipei")
 assert r.rejections.iloc[0].reason=="LIMIT_UP_LOCKED_BUY"
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
   config=config(permitted_usage_scope="RESEARCH_ONLY"))
 fill_times=list(r.fills.exec_ts)
 assert fill_times==sorted(fill_times)
 assert all(pd.Timestamp(trip.entry_ts)<=pd.Timestamp(trip.exit_ts) for trip in r.round_trips.itertuples())
 assert r.round_trips.iloc[0].side=="SHORT"

def test_cost_rounding_none_preserves_decimal_precision():
 p=replace(profile(),equity_commission_rate=.001425,minimum_commission=0,
   fee_rounding="NONE",tax_rounding="NONE")
 fee,tax=calculate_costs(61.1,1000,"SELL",p)
 assert fee==Decimal("87.0675")
 assert tax==Decimal("183.3")

def test_cost_rounding_floor_integer_is_explicit():
 p=replace(profile(),equity_commission_rate=.001425,minimum_commission=0,
   fee_rounding="FLOOR_INTEGER",tax_rounding="FLOOR_INTEGER")
 fee,tax=calculate_costs(61.1,1000,"SELL",p)
 assert fee==Decimal("87")
 assert tax==Decimal("183")

def test_unrounded_cost_precision_flows_into_realized_pnl():
 f=bars()
 p=replace(profile(),equity_commission_rate=.001425,minimum_commission=0,
   fee_rounding="NONE",tax_rounding="NONE")
 r=ExecutionEngine().run(f,[order("2024-01-02 09:00","BUY"),
   order("2024-01-02 09:02","SELL")],cost_profile=p,
   config=config(permitted_usage_scope="RESEARCH_ONLY"))
 assert r.fills.iloc[0].fee==Decimal("0.1425")
 assert r.fills.iloc[1].tax==Decimal("0.306")
 assert abs(float(r.round_trips.iloc[0].net_pnl)-1.40615)<1e-10

def test_day_tif_survives_to_first_session_close_after_order_time():
 f=bars()
 rows=[]
 for tm,p,v,day in [("2024-01-02 13:30",103,100,"2024-01-02"),
                     ("2024-01-03 08:55",104,10,"2024-01-03"),
                     ("2024-01-03 09:00",105,100,"2024-01-03")]:
  s=pd.Timestamp(tm,tz="Asia/Taipei")
  rows.append(dict(bar_start=s,bar_end=s+pd.Timedelta(minutes=1),available_at=s+pd.Timedelta(minutes=1),
    timezone="Asia/Taipei",session="RTH",symbol="2330",market="TW_EQUITY",contract="CASH",
    open=p,high=p+1,low=p-1,close=p,volume=v,data_exists=True,backtest_eligible=True,
    buy_execution_eligible=True,sell_execution_eligible=True,execution_rejection_reason="",
    session_open=pd.Timestamp(day+" 09:00",tz="Asia/Taipei"),
    session_close=pd.Timestamp(day+" 13:30",tz="Asia/Taipei")))
 f=pd.concat([f,pd.DataFrame(rows)],ignore_index=True)
 o=replace(order("2024-01-02 13:30","BUY",tif="DAY"),
   signal_time=pd.Timestamp("2024-01-02 13:25",tz="Asia/Taipei"),
   signal_available_at=pd.Timestamp("2024-01-02 13:30",tz="Asia/Taipei"))
 r=ExecutionEngine().run(f,[o],cost_profile=profile(),config=config())
 assert r.fills.iloc[0].exec_ts==pd.Timestamp("2024-01-03 09:00",tz="Asia/Taipei")
 assert list(r.rejections.bar_ts)==[
   pd.Timestamp("2024-01-02 13:30",tz="Asia/Taipei"),
   pd.Timestamp("2024-01-03 08:55",tz="Asia/Taipei")]
 assert list(r.rejections.reason)==["OUTSIDE_SESSION","OUTSIDE_SESSION"]

def test_day_tif_records_each_rejected_candidate_bar_before_later_fill():
 f=bars()
 f.loc[0,"volume"]=0
 f.loc[1,"buy_execution_eligible"]=False
 f.loc[1,"buy_execution_rejection_reason"]="LIMIT_UP_LOCKED_BUY"
 r=ExecutionEngine().run(f,[order("2024-01-02 09:00","BUY",tif="DAY")],
   cost_profile=profile(),config=config())
 assert r.fills.iloc[0].exec_ts==pd.Timestamp("2024-01-02 09:02",tz="Asia/Taipei")
 assert list(r.rejections.bar_ts)==[
   pd.Timestamp("2024-01-02 09:00",tz="Asia/Taipei"),
   pd.Timestamp("2024-01-02 09:01",tz="Asia/Taipei")]
 assert list(r.rejections.reason)==["ZERO_VOLUME_BAR","LIMIT_UP_LOCKED_BUY"]


def target_order(tm, side, target, *, order_id, signal_tm=None):
    signal_tm = signal_tm or tm
    return ExecutionOrder(
        symbol="2330", market="TW_EQUITY", contract="CASH",
        signal_time=pd.Timestamp(f"2024-01-02 {signal_tm}", tz="Asia/Taipei"),
        signal_available_at=pd.Timestamp(f"2024-01-02 {tm}", tz="Asia/Taipei"),
        order_time=pd.Timestamp(f"2024-01-02 {tm}", tz="Asia/Taipei"),
        side=side, strategy_id="s", experiment_id="e",
        target_position=target, time_in_force="DAY", order_id=order_id,
    )


def contract_bars(specs, *, session_close="09:40"):
    rows = []
    for tm, buy_ok, sell_ok, price in specs:
        start = pd.Timestamp(f"2024-01-02 {tm}", tz="Asia/Taipei")
        rows.append(dict(
            bar_start=start, bar_end=start + pd.Timedelta(minutes=5),
            available_at=start + pd.Timedelta(minutes=5), timezone="Asia/Taipei",
            session="RTH", symbol="2330", market="TW_EQUITY", contract="CASH",
            open=price, high=price + 1, low=price - 1, close=price, volume=10000,
            data_exists=True, backtest_eligible=True,
            buy_execution_eligible=buy_ok, sell_execution_eligible=sell_ok,
            execution_rejection_reason="LIMIT_UP_LOCKED_BUY" if not buy_ok else "",
            buy_execution_rejection_reason="LIMIT_UP_LOCKED_BUY" if not buy_ok else "",
            sell_execution_rejection_reason="LIMIT_DOWN_LOCKED_SELL" if not sell_ok else "",
            session_open=pd.Timestamp("2024-01-02 09:00", tz="Asia/Taipei"),
            session_close=pd.Timestamp(f"2024-01-02 {session_close}", tz="Asia/Taipei"),
        ))
    return pd.DataFrame(rows)


def test_g17_delayed_target_buy_is_balanced_by_later_flat_target():
    f = contract_bars([
        ("09:10", False, True, 60), ("09:15", False, True, 60),
        ("09:20", False, True, 60), ("09:25", True, True, 60),
    ])
    older = target_order("09:10", "BUY", 1000, order_id="old-buy", signal_tm="09:05")
    flatten = target_order("09:20", "SELL", 0, order_id="flat", signal_tm="09:15")
    r = ExecutionEngine().run(f, [older, flatten], cost_profile=profile(),
        config=config(permitted_usage_scope="RESEARCH_ONLY"))
    assert list(zip(r.fills.side, r.fills.qty, r.fills.exec_ts.dt.strftime("%H:%M"))) == [
        ("SELL", 1000, "09:20"), ("BUY", 1000, "09:25")]
    assert r.open_positions.empty
    assert r.round_trips.iloc[0].side == "SHORT"
    assert r.order_events.loc[
        r.order_events.order_id.eq("flat"), "reason_code"
    ].tolist()[0] == "PENDING_TARGET_INCLUDED"
    rebalance = r.order_events[
        (r.order_events.order_id == "old-buy")
        & (r.order_events.event == "TARGET_ORDER_REBALANCED")
    ].iloc[0]
    assert rebalance.related_order_id == "flat"
    assert rebalance.reason_code == "NEWER_TARGET_RECONCILES_LIVE_INTENT"
    assert list(r.rejections.loc[
        r.rejections.order_id.eq("old-buy"), "bar_ts"
    ].dt.strftime("%H:%M")) == ["09:10", "09:15", "09:20"]


def test_g17_later_target_nets_against_pending_target_quantity():
    f = contract_bars([
        ("09:10", False, True, 60), ("09:15", False, True, 60),
        ("09:20", True, True, 60),
    ])
    older = target_order("09:10", "BUY", 1000, order_id="old-buy", signal_tm="09:05")
    replace_with_500 = target_order("09:15", "SELL", 500, order_id="new-target", signal_tm="09:10")
    r = ExecutionEngine().run(f, [older, replace_with_500], cost_profile=profile(),
        config=config(permitted_usage_scope="RESEARCH_ONLY"))
    assert list(zip(r.fills.side, r.fills.qty)) == [("SELL", 500), ("BUY", 1000)]
    assert r.open_positions.iloc[0].side == "LONG"
    assert r.open_positions.iloc[0].qty == 500


def test_g17_same_timestamp_target_orders_have_deterministic_ordering():
    f = contract_bars([("09:10", True, True, 60)])
    first = target_order("09:10", "BUY", 1000, order_id="a-target", signal_tm="09:05")
    second = target_order("09:10", "SELL", 500, order_id="b-target", signal_tm="09:05")
    def run(input_orders):
        return ExecutionEngine().run(f, input_orders, cost_profile=profile(),
            config=config(permitted_usage_scope="RESEARCH_ONLY"))
    a, b = run([first, second]), run([second, first])
    assert list(zip(a.fills.order_id, a.fills.side, a.fills.qty)) == list(
        zip(b.fills.order_id, b.fills.side, b.fills.qty))
    assert list(zip(a.fills.side, a.fills.qty)) == [("BUY", 1000), ("SELL", 500)]


def test_g17_explicit_quantity_orders_remain_independent_of_target_orders():
    f = contract_bars([("09:10", True, True, 60), ("09:15", True, True, 60)])
    explicit = order("2024-01-02 09:10", "BUY", 100)
    target = target_order("09:15", "BUY", 500, order_id="target", signal_tm="09:10")
    r = ExecutionEngine().run(f, [explicit, target], cost_profile=profile(),
        config=config(permitted_usage_scope="RESEARCH_ONLY"))
    assert list(r.fills.qty) == [100, 400]
    assert list(r.order_events.loc[
        (r.order_events.order_id.eq("order-1"))
        & (r.order_events.event.eq("ORDER_ACTIVATED")), "reason_code"
    ]) == ["EXPLICIT_QUANTITY_INDEPENDENT"]


def test_g14b_order_expires_before_bar_start_equal_to_expiry():
    f = contract_bars([
        ("09:10", False, True, 60), ("09:15", False, True, 60),
        ("09:20", True, True, 60),
    ], session_close="09:20")
    # The final row is the next session opening exactly at the prior close/expiry.
    f.loc[2, "session"] = "RTH_NEXT"
    f.loc[2, "session_open"] = pd.Timestamp("2024-01-02 09:20", tz="Asia/Taipei")
    f.loc[2, "session_close"] = pd.Timestamp("2024-01-02 09:40", tz="Asia/Taipei")
    o = target_order("09:10", "BUY", 1000, order_id="day-boundary", signal_tm="09:05")
    r = ExecutionEngine().run(f, [o], cost_profile=profile(),
        config=config(permitted_usage_scope="RESEARCH_ONLY"))
    assert r.fills.empty
    expired = r.rejections[r.rejections.reason.eq("ORDER_EXPIRED")].iloc[0]
    assert pd.isna(expired.bar_ts)
    assert expired.expiry_ts == pd.Timestamp("2024-01-02 09:20", tz="Asia/Taipei")
    assert list(r.rejections.loc[
        r.rejections.reason.eq("LIMIT_UP_LOCKED_BUY"), "bar_ts"
    ].dt.strftime("%H:%M")) == ["09:10", "09:15"]


def test_g14b_day_order_can_fill_strictly_before_expiry():
    f = contract_bars([
        ("09:10", False, True, 60), ("09:15", True, True, 60),
        ("09:20", True, True, 60),
    ], session_close="09:20")
    f.loc[2, "session"] = "RTH_NEXT"
    f.loc[2, "session_open"] = pd.Timestamp("2024-01-02 09:20", tz="Asia/Taipei")
    f.loc[2, "session_close"] = pd.Timestamp("2024-01-02 09:40", tz="Asia/Taipei")
    o = target_order("09:10", "BUY", 1000, order_id="day-before-boundary", signal_tm="09:05")
    r = ExecutionEngine().run(f, [o], cost_profile=profile(),
        config=config(permitted_usage_scope="RESEARCH_ONLY"))
    assert r.fills.iloc[0].exec_ts == pd.Timestamp("2024-01-02 09:15", tz="Asia/Taipei")
    assert not r.rejections.reason.eq("ORDER_EXPIRED").any()
