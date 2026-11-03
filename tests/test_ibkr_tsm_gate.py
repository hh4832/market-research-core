from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from market_research_core.costs import CostProfile, calculate_cost_components
from market_research_core.execution import ExecutionConfig, ExecutionEngine, ExecutionOrder
from market_research_core.ibkr_tsm import NYSE_CALENDAR_VERSION, apply_tsm_session_mask


BASE_FIELDS = {
    "equity_commission_rate", "minimum_commission", "sell_tax_rate",
    "futures_fee_per_contract_per_side", "futures_transaction_tax_rate",
    "contract_multiplier", "tick_size", "slippage_ticks", "fee_rounding", "tax_rounding",
}


def profile(**overrides):
    values = dict(
        profile_id="tsm-synthetic", version="test", instrument_type="EQUITY",
        approval_status="DRAFT", source_evidence="synthetic regression fixture",
        basis="ASSUMED", equity_commission_rate=0, minimum_commission=0,
        sell_tax_rate=0, futures_fee_per_contract_per_side=0,
        futures_transaction_tax_rate=0, contract_multiplier=1, tick_size=0.01,
        slippage_ticks=0, fee_rounding="NONE", tax_rounding="NONE",
        component_basis={name: "ASSUMED" for name in BASE_FIELDS},
    )
    values.update(overrides)
    return CostProfile(**values)


def execution_bars(*, available_policy="OBSERVED", volumes=(100, 100, 100), prices=(100, 101, 102)):
    rows = []
    for minute, volume, price in zip((0, 1, 2), volumes, prices):
        start = pd.Timestamp("2024-01-02 09:00", tz="Asia/Taipei") + pd.Timedelta(minutes=minute)
        rows.append({
            "bar_start": start, "bar_end": start + pd.Timedelta(minutes=1),
            "available_at": start + pd.Timedelta(minutes=1),
            "available_at_policy": available_policy,
            "timezone": "Asia/Taipei", "session": "RTH",
            "symbol": "TSM", "market": "IBKR_EQUITY", "contract": "CASH",
            "open": price, "high": price + 1, "low": price - 1, "close": price,
            "volume": volume, "data_exists": True, "backtest_eligible": True,
            "buy_execution_eligible": True, "sell_execution_eligible": True,
            "execution_rejection_reason": "", "session_open": pd.Timestamp("2024-01-02 09:00", tz="Asia/Taipei"),
            "session_close": pd.Timestamp("2024-01-02 16:00", tz="Asia/Taipei"),
        })
    return pd.DataFrame(rows)


def equity_order(side, *, minute, quantity, signal_minute=None):
    signal_minute = minute if signal_minute is None else signal_minute
    day = pd.Timestamp("2024-01-02 09:00", tz="Asia/Taipei")
    return ExecutionOrder(
        symbol="TSM", market="IBKR_EQUITY", contract="CASH",
        signal_time=day + pd.Timedelta(minutes=signal_minute - 1),
        signal_available_at=day + pd.Timedelta(minutes=minute),
        order_time=day + pd.Timedelta(minutes=minute), side=side,
        quantity=quantity, strategy_id="synthetic", experiment_id="m3a1-test",
    )


def test_unknown_timestamp_and_availability_remain_fail_closed():
    frame = pd.DataFrame({"timestamp": pd.to_datetime(["2024-11-29T14:30:00Z"], utc=True)})
    result = apply_tsm_session_mask(frame, timestamp_label="UNKNOWN", availability_policy="UNKNOWN")
    assert result.iloc[0].session == "UNKNOWN"
    assert pd.isna(result.iloc[0].bar_start)
    assert pd.isna(result.iloc[0].bar_end)
    assert pd.isna(result.iloc[0].available_at)
    assert not result.iloc[0].buy_execution_eligible
    assert not result.iloc[0].sell_execution_eligible


def test_nyse_mask_handles_early_close_eth_dst_and_label_equivalence():
    starts = pd.to_datetime([
        "2024-11-29T14:29:00Z", "2024-11-29T14:30:00Z",
        "2024-11-29T17:59:00Z", "2024-11-29T18:00:00Z",
        "2024-11-30T00:59:00Z", "2024-11-30T01:00:00Z",
    ], utc=True)
    start_frame = pd.DataFrame({"timestamp": starts})
    from_start = apply_tsm_session_mask(
        start_frame, timestamp_label="START", availability_policy="BAR_END_STRICT_LATER"
    )
    expected = ["PRE_MARKET", "RTH", "RTH", "AFTER_HOURS", "AFTER_HOURS", "OUTSIDE_SESSION"]
    assert list(from_start.session) == expected
    assert (from_start.available_at == from_start.bar_end).all()
    assert not from_start.buy_execution_eligible.any()
    assert not from_start.sell_execution_eligible.any()
    assert set(from_start.session_close.dropna()) == {pd.Timestamp("2024-11-29T18:00:00Z")}

    end_frame = pd.DataFrame({"timestamp": starts + pd.Timedelta(minutes=1)})
    from_end = apply_tsm_session_mask(
        end_frame, timestamp_label="END", availability_policy="BAR_END_STRICT_LATER"
    )
    assert list(from_end.session) == expected
    assert list(from_end.bar_start) == list(from_start.bar_start)

    dst = apply_tsm_session_mask(
        pd.DataFrame({"timestamp": pd.to_datetime(["2024-03-11T13:30:00Z"], utc=True)}),
        timestamp_label="START", availability_policy="BAR_END_STRICT_LATER",
    )
    assert dst.iloc[0].session == "RTH"
    assert dst.iloc[0].session_open == pd.Timestamp("2024-03-11T13:30:00Z")
    assert NYSE_CALENDAR_VERSION == "5.5.0"


def test_market_holiday_is_outside_session_not_inferred_as_rth():
    result = apply_tsm_session_mask(
        pd.DataFrame({"timestamp": pd.to_datetime(["2024-11-28T14:30:00Z"], utc=True)}),
        timestamp_label="START", availability_policy="BAR_END_STRICT_LATER",
    )
    assert result.iloc[0].session == "OUTSIDE_SESSION"
    assert pd.isna(result.iloc[0].session_open)
    assert not result.iloc[0].buy_execution_eligible


def test_bar_end_policy_uses_strictly_later_execution_and_is_research_only():
    bars = execution_bars(available_policy="BAR_END_STRICT_LATER")
    order = equity_order("BUY", minute=1, quantity=1, signal_minute=1)
    config = ExecutionConfig(
        initial_cash=10_000, bar_label="START", session_semantics="CONFIRMED",
        timestamp_timezone="Asia/Taipei", available_at_policy="BAR_END_STRICT_LATER",
        permitted_usage_scope="BACKTEST",
    )
    result = ExecutionEngine().run(bars, [order], cost_profile=profile(), config=config)
    assert result.fills.iloc[0].exec_ts == pd.Timestamp("2024-01-02 09:02", tz="Asia/Taipei")
    assert result.provenance["available_at_policy"] == "BAR_END_STRICT_LATER"
    assert result.status == "RESEARCH_ONLY"
    with pytest.raises(ValueError, match="forbids same-timestamp"):
        ExecutionConfig(
            initial_cash=10_000, bar_label="START", session_semantics="CONFIRMED",
            timestamp_timezone="Asia/Taipei", available_at_policy="BAR_END_STRICT_LATER",
            same_timestamp_open_assumption=True,
        )


def test_per_share_minimum_bps_spread_and_sell_regulatory_fee():
    p = profile(
        minimum_commission=1.0,
        equity_commission_per_share=0.005,
        equity_slippage_bps=2,
        half_spread_bps=1,
        sell_regulatory_fee_rate=0.001,
        component_basis={
            **{name: "ASSUMED" for name in BASE_FIELDS},
            "equity_commission_per_share": "ASSUMED",
            "equity_slippage_bps": "ASSUMED",
            "half_spread_bps": "ASSUMED",
            "sell_regulatory_fee_rate": "ASSUMED",
        },
        component_evidence={
            name: "synthetic assumption only; not an approved broker rate"
            for name in ("equity_commission_per_share", "equity_slippage_bps", "half_spread_bps", "sell_regulatory_fee_rate")
        },
        component_approval_status={
            name: "PENDING"
            for name in ("equity_commission_per_share", "equity_slippage_bps", "half_spread_bps", "sell_regulatory_fee_rate")
        },
    )
    fee_buy, tax_buy, regulatory_buy = calculate_cost_components(100, 100, "BUY", p)
    fee_sell, tax_sell, regulatory_sell = calculate_cost_components(100, 100, "SELL", p)
    assert fee_buy == Decimal("1")  # minimum commission
    assert fee_sell == Decimal("1")
    assert tax_buy == tax_sell == Decimal("0")
    assert regulatory_buy == Decimal("0")
    assert regulatory_sell == Decimal("10")
    from market_research_core.costs import adverse_fill_price
    assert adverse_fill_price(100, "BUY", p) == 100.03
    assert adverse_fill_price(100, "SELL", p) == 99.97


def test_new_cost_components_flow_through_trade_ledger():
    p = profile(
        equity_commission_per_share=0.01,
        sell_regulatory_fee_rate=0.001,
        component_basis={
            **{name: "ASSUMED" for name in BASE_FIELDS},
            "equity_commission_per_share": "ASSUMED",
            "sell_regulatory_fee_rate": "ASSUMED",
        },
        component_evidence={
            "equity_commission_per_share": "synthetic fixture",
            "sell_regulatory_fee_rate": "synthetic fixture",
        },
        component_approval_status={
            "equity_commission_per_share": "PENDING",
            "sell_regulatory_fee_rate": "PENDING",
        },
    )
    config = ExecutionConfig(
        initial_cash=10_000, bar_label="START", session_semantics="CONFIRMED",
        timestamp_timezone="Asia/Taipei", same_timestamp_open_assumption=True,
        permitted_usage_scope="RESEARCH_ONLY",
    )
    result = ExecutionEngine().run(
        execution_bars(),
        [equity_order("BUY", minute=0, quantity=10, signal_minute=0),
         equity_order("SELL", minute=1, quantity=10, signal_minute=1)],
        cost_profile=p, config=config,
    )
    assert list(result.fills.regulatory_fee) == [Decimal("0"), Decimal("1.01")]
    assert result.round_trips.iloc[0].net_pnl == pytest.approx(8.79)
    assert result.status == "RESEARCH_ONLY"
    assert result.provenance["cost_profile_components"]["commission_per_share"]["source_evidence"] == "synthetic fixture"
    assert result.provenance["cost_profile_components"]["commission_per_share"]["approval_status"] == "PENDING"


def test_volume_participation_cap_rejects_full_quantity_without_partial_fill():
    p = profile(
        volume_participation_cap=0.1,
        component_basis={
            **{name: "ASSUMED" for name in BASE_FIELDS},
            "volume_participation_cap": "ASSUMED",
        },
        component_evidence={"volume_participation_cap": "synthetic fixture"},
        component_approval_status={"volume_participation_cap": "PENDING"},
    )
    result = ExecutionEngine().run(
        execution_bars(volumes=(100, 100, 100)),
        [equity_order("BUY", minute=0, quantity=11, signal_minute=0)],
        cost_profile=p,
        config=ExecutionConfig(
            initial_cash=10_000, bar_label="START", session_semantics="CONFIRMED",
            timestamp_timezone="Asia/Taipei", same_timestamp_open_assumption=True,
            permitted_usage_scope="RESEARCH_ONLY",
        ),
    )
    assert result.fills.empty
    assert result.rejections.iloc[0].reason == "VOLUME_PARTICIPATION_EXCEEDED"
