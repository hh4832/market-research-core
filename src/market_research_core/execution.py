"""Point-in-time, signal-driven execution contract.

The engine consumes normalized bars and explicit orders. It never generates signals.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from datetime import datetime
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable, Literal

import pandas as pd

from .costs import CostProfile, calculate_fill_costs, adverse_fill_price
from .temporal import TemporalPolicy, validate_conservative_bars


@dataclass(frozen=True)
class ExecutionOrder:
    symbol: str
    market: Literal["TW_EQUITY", "IBKR_EQUITY", "IBKR_FUTURES", "TXF"]
    contract: str
    signal_time: Any
    signal_available_at: Any
    order_time: Any
    side: Literal["BUY", "SELL"]
    strategy_id: str
    experiment_id: str
    quantity: float | None = None
    target_position: float | None = None
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    time_in_force: Literal["NEXT_BAR", "DAY"] = "NEXT_BAR"
    limit_price: float | None = None
    order_id: str | None = None

    def __post_init__(self) -> None:
        if (self.quantity is None) == (self.target_position is None):
            raise ValueError("provide exactly one of quantity or target_position")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.target_position is not None and not math.isfinite(self.target_position):
            raise ValueError("target_position must be finite")
        if self.order_type == "LIMIT" and (self.limit_price is None or self.limit_price <= 0):
            raise ValueError("LIMIT orders require a positive limit_price")
        if self.order_type == "MARKET" and self.limit_price is not None:
            raise ValueError("MARKET orders cannot include limit_price")
        if not self.strategy_id or not self.experiment_id:
            raise ValueError("strategy_id and experiment_id are required")


@dataclass(frozen=True)
class ExecutionConfig:
    initial_cash: float
    bar_label: Literal["START", "END", "UNKNOWN"]
    session_semantics: Literal["CONFIRMED", "UNKNOWN"]
    timestamp_timezone: str
    same_timestamp_open_assumption: bool = False
    available_at_policy: Literal["OBSERVED", "BAR_END_STRICT_LATER", "UNKNOWN", "CONSERVATIVE_LABEL_AGNOSTIC"] = "OBSERVED"
    initial_margin_per_contract: float | None = None
    max_gross_notional: float | None = None
    exclusion_windows: tuple[tuple[Any, Any, str], ...] = ()
    core_commit_sha: str = "UNKNOWN"
    dataset_snapshot_id: str = "UNKNOWN"
    dataset_producer_sha: str = "UNKNOWN"
    dataset_verdict: str = "UNKNOWN"
    permitted_usage_scope: str = "RESEARCH_ONLY"
    spec_sha256: str = "UNKNOWN"
    parameters: dict[str, Any] = field(default_factory=dict)
    cost_multiplier: float = 1.0
    seed: int | None = None
    window_start: Any = None
    window_end: Any = None
    temporal_policy: TemporalPolicy | None = None

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not math.isfinite(self.cost_multiplier) or self.cost_multiplier < 0:
            raise ValueError("cost_multiplier must be finite and non-negative")
        conservative = self.available_at_policy == 'CONSERVATIVE_LABEL_AGNOSTIC'
        if conservative:
            if not isinstance(self.temporal_policy, TemporalPolicy) or self.bar_label != 'UNKNOWN':
                raise ValueError('conservative mode requires explicit TemporalPolicy and UNKNOWN source label')
            if self.same_timestamp_open_assumption or self.timestamp_timezone != 'America/New_York':
                raise ValueError('conservative mode forbids same-time execution and requires NYSE timezone')
        elif self.temporal_policy is not None:
            raise ValueError('temporal_policy requires its conservative mode')
        if (self.bar_label == "UNKNOWN" and not conservative) or self.session_semantics == "UNKNOWN":
            raise ValueError("formal execution is blocked when bar label or session semantics are UNKNOWN")
        if self.available_at_policy not in {"OBSERVED", "BAR_END_STRICT_LATER", "UNKNOWN", "CONSERVATIVE_LABEL_AGNOSTIC"}:
            raise ValueError("unsupported available_at_policy")
        if self.available_at_policy == "BAR_END_STRICT_LATER" and self.same_timestamp_open_assumption:
            raise ValueError("BAR_END_STRICT_LATER forbids same-timestamp bar-open execution")
        if self.timestamp_timezone != "Asia/Taipei" and self.timestamp_timezone != "America/New_York" and not self.timestamp_timezone:
            raise ValueError("timestamp_timezone must be explicit")


@dataclass
class ExecutionResult:
    fills: pd.DataFrame
    round_trips: pd.DataFrame
    rejections: pd.DataFrame
    equity_curve: pd.DataFrame
    open_positions: pd.DataFrame
    invalidated_round_trips: pd.DataFrame
    order_events: pd.DataFrame
    provenance: dict[str, Any]
    status: str

    @property
    def ledger(self) -> pd.DataFrame:
        """One row per fill; monetary component debits and temporal evidence."""
        return self.fills.copy()


_REQUIRED_COLUMNS = {
    "bar_start", "bar_end", "available_at", "timezone", "session",
    "symbol", "market", "contract", "open", "high", "low", "close", "volume",
    "data_exists", "backtest_eligible", "buy_execution_eligible",
    "sell_execution_eligible", "execution_rejection_reason",
    "session_open", "session_close",
}


def _ts(value: Any, timezone: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(timezone)
    return ts


def _money(value: float | Decimal) -> float:
    return float(value)


def _in_window(ts: pd.Timestamp, windows: tuple[tuple[Any, Any, str], ...], timezone: str) -> str | None:
    for start, end, label in windows:
        if _ts(start, timezone) <= ts < _ts(end, timezone):
            return label
    return None


def _canonical_sha(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ExecutionEngine:
    """FIFO long/short simulator with explicit session, PIT, eligibility and cost gates."""

    def run(
        self,
        bars: pd.DataFrame,
        orders: Iterable[ExecutionOrder],
        *,
        cost_profile: CostProfile,
        config: ExecutionConfig,
    ) -> ExecutionResult:
        missing = _REQUIRED_COLUMNS - set(bars.columns)
        if missing:
            raise ValueError(f"bars missing required execution columns: {sorted(missing)}")
        if config.temporal_policy is not None:
            validate_conservative_bars(bars, config.temporal_policy)
        frame = bars.copy()
        if "available_at_policy" in frame.columns:
            row_policies = set(frame["available_at_policy"].dropna().astype(str))
            if row_policies and row_policies != {config.available_at_policy}:
                raise ValueError("available_at_policy does not match bar provenance")
        frame["bar_start"] = pd.to_datetime(frame["bar_start"], utc=True).dt.tz_convert(config.timestamp_timezone)
        frame["bar_end"] = pd.to_datetime(frame["bar_end"], utc=True).dt.tz_convert(config.timestamp_timezone)
        frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True).dt.tz_convert(config.timestamp_timezone)
        frame["session_open"] = pd.to_datetime(frame["session_open"], utc=True).dt.tz_convert(config.timestamp_timezone)
        frame["session_close"] = pd.to_datetime(frame["session_close"], utc=True).dt.tz_convert(config.timestamp_timezone)
        frame = frame.sort_values(["bar_start", "symbol", "contract"], kind="stable").reset_index(drop=True)
        if frame.duplicated(["symbol", "contract", "bar_start"]).any():
            raise ValueError("duplicate bars for symbol/contract/bar_start")
        if frame[["bar_start", "bar_end", "available_at", "session_open", "session_close"]].isna().any().any():
            raise ValueError("execution timing/session fields contain UNKNOWN values")
        if (frame["bar_end"] <= frame["bar_start"]).any():
            raise ValueError("bar_end must be after bar_start")
        if (frame["available_at"] < frame["bar_end"]).any():
            raise ValueError("available_at cannot precede bar_end")
        if config.available_at_policy == "BAR_END_STRICT_LATER" and not frame["available_at"].eq(frame["bar_end"]).all():
            raise ValueError("BAR_END_STRICT_LATER requires available_at == bar_end")
        if frame["session"].astype(str).str.upper().eq("UNKNOWN").any():
            raise ValueError("formal execution is blocked for UNKNOWN session rows")

        orders = sorted(
            list(orders),
            key=lambda o: (_ts(o.order_time, config.timestamp_timezone), o.order_id or "", _canonical_sha(asdict(o))),
        )
        orders_input = list(orders)
        fills: list[dict[str, Any]] = []
        rejects: list[dict[str, Any]] = []
        trips: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        lots: dict[tuple[str, str], list[dict[str, Any]]] = {}
        cash = float(config.initial_cash)
        filled_positions: dict[tuple[str, str, str], float] = {}
        order_events: list[dict[str, Any]] = []
        assumptions = ["FIFO lot matching", "orders execute at bar open for market orders", "end-of-data positions remain open"]
        if config.available_at_policy == "BAR_END_STRICT_LATER":
            assumptions.append("available_at is conservatively modeled at bar_end; actual historical publication latency is UNKNOWN; equal-time next-bar opens are excluded")

        if config.temporal_policy is not None:
            assumptions.append(config.temporal_policy.assumption)

        # Build each order's candidate bar set first, then advance all candidate
        # orders on the shared bar_start timeline. State changes happen only at
        # the bar where a fill occurs, never in order-creation order.
        records: list[dict[str, Any]] = []
        for order_idx, order in enumerate(orders):
            oid = order.order_id or f"order-{order_idx + 1}"
            signal_time = _ts(order.signal_time, config.timestamp_timezone)
            available = _ts(order.signal_available_at, config.timestamp_timezone)
            order_time = _ts(order.order_time, config.timestamp_timezone)
            if config.temporal_policy is not None:
                signal_rows = frame[frame['modeled_available_at'].eq(signal_time)]
                if signal_rows.empty or not signal_rows['signal_eligible'].all() or available < signal_rows['modeled_available_at'].max():
                    rejects.append({'order_id': oid, 'reason': 'PIT_VIOLATION',
                                    'detail': 'signal must reference a complete observed conservative bucket by modeled completion'})
                    continue
                if order.order_type != 'MARKET':
                    rejects.append({'order_id': oid, 'reason': 'UNSUPPORTED_CONSERVATIVE_ORDER_TYPE',
                                    'detail': 'intrabar LIMIT simulation is not approved under unknown labels'})
                    continue
            if available < signal_time or order_time < available:
                rejects.append({"order_id": oid, "reason": "PIT_VIOLATION", "detail": "signal_available_at/order_time precedes prior information"})
                continue
            if order.market in ("TXF", "IBKR_FUTURES") and config.initial_margin_per_contract is None:
                raise ValueError("TXF execution requires explicit initial_margin_per_contract")
            if order.market in ("TXF", "IBKR_FUTURES") and cost_profile.instrument_type != "FUTURES":
                raise ValueError("TXF requires a FUTURES CostProfile")
            if order.market not in ("TXF", "IBKR_FUTURES") and cost_profile.instrument_type != "EQUITY":
                raise ValueError("equity market requires an EQUITY CostProfile")

            matching = frame[
                (frame["symbol"] == order.symbol)
                & (frame["market"] == order.market)
                & (frame["contract"] == order.contract)
                & (frame["bar_start"] >= available)
                & (frame["bar_start"] >= order_time)
                & (frame["bar_end"] > signal_time)
            ]
            if not config.same_timestamp_open_assumption:
                matching = matching[matching["bar_start"] > max(available, order_time)]
            if config.temporal_policy is not None:
                matching = matching[matching['session_close'].eq(signal_rows['session_close'].max())]
            expiry = None
            if order.time_in_force == "NEXT_BAR":
                matching = matching.iloc[:1]
            elif not matching.empty:
                session_ends = matching.loc[matching["session_close"] > order_time, "session_close"]
                if session_ends.empty:
                    matching = matching.iloc[0:0]
                else:
                    expiry = pd.Timestamp(session_ends.min())
                    matching = matching[matching["bar_start"] < expiry]

            records.append({
                "idx": order_idx, "oid": oid, "order": order,
                "signal_time": signal_time, "available": available, "order_time": order_time,
                "signal_source_timestamp": signal_rows['source_timestamp'].iloc[0] if config.temporal_policy else None,
                "target_key": (order.strategy_id, order.symbol, order.contract) if order.target_position is not None else None,
                "expiry": expiry,
                "candidate_rows": {pd.Timestamp(row["bar_start"]): row for _, row in matching.iterrows()},
                "last_reason": "UNFILLED_AT_END_OF_DATA", "done": False,
                "activated": False,
                "side": order.side if order.target_position is None else None,
                "qty": float(order.quantity) if order.quantity is not None else None,
                "signed_remaining": 0.0,
            })

        bars_by_time: dict[pd.Timestamp, dict[tuple[str, str, str], pd.Series]] = {}
        for _, bar in frame.iterrows():
            ts = pd.Timestamp(bar["bar_start"])
            bars_by_time.setdefault(ts, {})[(bar["symbol"], bar["market"], bar["contract"])] = bar

        event_times = set(bars_by_time)
        event_times.update(record["order_time"] for record in records)
        event_times.update(record["expiry"] for record in records if record["expiry"] is not None)
        for event_ts in sorted(event_times):
            for record in records:
                if not record["done"] and record["expiry"] is not None and record["expiry"] <= event_ts:
                    rejects.append({"order_id": record["oid"], "bar_ts": None,
                        "expiry_ts": record["expiry"], "reason": "ORDER_EXPIRED",
                        "detail": "DAY order expired at the first session close after order_time"})
                    order_events.append({"order_id": record["oid"], "event_ts": event_ts,
                        "event": "ORDER_EXPIRED", "reason_code": "TIF_EXPIRY"})
                    record["done"] = True
                    record["signed_remaining"] = 0.0

            activating = sorted((record for record in records
                if not record["done"] and not record["activated"] and record["order_time"] == event_ts),
                key=lambda record: (record["order"].order_id or "", _canonical_sha(asdict(record["order"]))))
            for record in activating:
                order = record["order"]
                target_key = record["target_key"]
                if target_key is None:
                    record["activated"] = True
                    record["signed_remaining"] = float(order.quantity) if order.side == "BUY" else -float(order.quantity)
                    order_events.append({"order_id": record["oid"], "event_ts": event_ts,
                        "event": "ORDER_ACTIVATED", "reason_code": "EXPLICIT_QUANTITY_INDEPENDENT"})
                    continue
                filled_position = filled_positions.get(target_key, 0.0)
                pending_targets = [prior for prior in records
                    if prior is not record and prior["activated"] and not prior["done"]
                    and prior["target_key"] == target_key]
                pending_signed = sum(prior["signed_remaining"] for prior in pending_targets)
                delta = float(order.target_position) - (filled_position + pending_signed)
                for prior in pending_targets:
                    order_events.append({
                        "order_id": prior["oid"], "related_order_id": record["oid"],
                        "event_ts": event_ts, "event": "TARGET_ORDER_REBALANCED",
                        "reason_code": "NEWER_TARGET_RECONCILES_LIVE_INTENT",
                        "pending_signed_qty": prior["signed_remaining"],
                        "action": "RETAIN_PENDING_FOR_ORACLE_COMPATIBILITY",
                    })
                record["activated"] = True
                if abs(delta) < 1e-12:
                    record["done"] = True
                    order_events.append({"order_id": record["oid"], "event_ts": event_ts,
                        "event": "TARGET_ORDER_NOOP", "reason_code": "TARGET_ALREADY_REPRESENTED",
                        "desired_target": float(order.target_position), "filled_position": filled_position,
                        "pending_signed_qty": pending_signed})
                    continue
                side = "BUY" if delta > 0 else "SELL"
                if side != order.side:
                    rejects.append({"order_id": record["oid"], "reason": "TARGET_SIDE_MISMATCH",
                        "detail": f"target requires {side}"})
                    order_events.append({"order_id": record["oid"], "event_ts": event_ts,
                        "event": "ORDER_REJECTED", "reason_code": "TARGET_SIDE_MISMATCH"})
                    record["done"] = True
                    continue
                record["side"], record["qty"], record["signed_remaining"] = side, abs(delta), delta
                order_events.append({"order_id": record["oid"], "event_ts": event_ts,
                    "event": "TARGET_ORDER_ACTIVATED",
                    "reason_code": "PENDING_TARGET_INCLUDED" if abs(pending_signed) > 1e-12 else "FILLED_POSITION_ONLY",
                    "desired_target": float(order.target_position), "filled_position": filled_position,
                    "pending_signed_qty": pending_signed, "projected_position": filled_position + pending_signed,
                    "signed_delta": delta})

            live = sorted((record for record in records
                if not record["done"] and record["activated"] and event_ts in record["candidate_rows"]),
                key=lambda record: (record["order_time"], record["order"].order_id or "",
                    _canonical_sha(asdict(record["order"]))))
            for record in live:
                order = record["order"]
                oid = record["oid"]
                bar = record["candidate_rows"][event_ts]
                key = (order.symbol, order.contract)
                target_key = record["target_key"]
                side, qty = record["side"], record["qty"]
                if side != order.side:
                    rejects.append({"order_id": oid, "reason": "TARGET_SIDE_MISMATCH", "detail": f"target requires {side}"})
                    record["done"] = True
                    continue

                reason: str | None = None
                cost_meta = cost_profile.configuration_metadata
                fill_date = bar['bar_start'].date().isoformat()
                cost_date_ok = not cost_meta or (cost_meta['effective_from'] <= fill_date and
                                (cost_meta['effective_to'] is None or fill_date <= cost_meta['effective_to']))
                daytrade_ok = not cost_meta.get('daytrade_evidence') or side != 'SELL' or (
                    cost_meta['as_of'] == fill_date and qty <= sum(
                        lot['signed_qty'] for lot in lots.get(key, [])
                        if lot['signed_qty'] > 0 and lot['entry_ts'].date() == bar['bar_start'].date()))
                if bar["bar_start"] < bar["session_open"] or bar["bar_start"] >= bar["session_close"]:
                    reason = "OUTSIDE_SESSION"
                elif not cost_date_ok:
                    reason = 'COST_RATE_OUTSIDE_EFFECTIVE_DATES'
                elif cost_meta and (order.symbol != cost_meta['symbol'] or order.market != cost_meta['applicability']['market']):
                    reason = 'COST_PROFILE_MARKET_MISMATCH'
                elif not daytrade_ok:
                    reason = 'DAYTRADE_TAX_NOT_QUALIFIED'
                elif not bool(bar["data_exists"]) or not bool(bar["backtest_eligible"]):
                    reason = "BAR_NOT_ELIGIBLE"
                elif float(bar["volume"]) <= 0:
                    reason = "ZERO_VOLUME_BAR"
                elif cost_profile.volume_participation_cap is not None and qty > float(bar["volume"]) * cost_profile.volume_participation_cap:
                    reason = "VOLUME_PARTICIPATION_EXCEEDED"
                else:
                    eligible_col = "buy_execution_eligible" if side == "BUY" else "sell_execution_eligible"
                    if not bool(bar[eligible_col]):
                        reason_col = f"{side.lower()}_execution_rejection_reason"
                        reason_value = bar.get(reason_col, bar["execution_rejection_reason"])
                        reason = str(reason_value) if pd.notna(reason_value) and str(reason_value) else f"{side}_NOT_ELIGIBLE"
                    else:
                        exclusion = _in_window(bar["bar_start"], config.exclusion_windows, config.timestamp_timezone)
                        if exclusion:
                            reason = "BAR_IN_EXCLUSION_WINDOW"
                        elif order.order_type == "LIMIT" and (
                            (side == "BUY" and float(bar["low"]) > float(order.limit_price))
                            or (side == "SELL" and float(bar["high"]) < float(order.limit_price))
                        ):
                            reason = "LIMIT_NOT_TOUCHED"

                if reason is not None:
                    record["last_reason"] = reason
                    rejects.append({
                        "order_id": oid, "bar_ts": bar["bar_start"], "reason": reason,
                        "detail": "candidate bar was not eligible for this order",
                    })
                    # NEXT_BAR has a single candidate. DAY keeps waiting after
                    # ordinary bar-level rejections until fill or expiry.
                    if order.time_in_force == "NEXT_BAR" or reason == "BAR_IN_EXCLUSION_WINDOW":
                        record["done"] = True
                        record["signed_remaining"] = 0.0
                    continue

                if order.order_type == "LIMIT":
                    raw_price = min(float(bar["open"]), float(order.limit_price)) if side == "BUY" else max(float(bar["open"]), float(order.limit_price))
                else:
                    raw_price = float(bar["open"])
                fill_price = adverse_fill_price(raw_price, side, cost_profile, config.cost_multiplier)
                if order.order_type == "LIMIT":
                    fill_price = min(fill_price, float(order.limit_price)) if side == "BUY" else max(fill_price, float(order.limit_price))
                cost_multiplier = Decimal(str(config.cost_multiplier))
                cost_parts = {k: v * cost_multiplier for k, v in calculate_fill_costs(fill_price, qty, side, cost_profile).items()}
                fee = cost_parts['commission'] + cost_parts['exchange_fee'] + cost_parts['clearing_fee']
                tax, regulatory_fee = cost_parts['transaction_tax'], cost_parts['regulatory_fee']
                signed_delta = qty if side == "BUY" else -qty
                current_net = sum(lot["signed_qty"] for lot in lots.get(key, []))
                resulting_net = current_net + signed_delta
                multiplier = cost_profile.contract_multiplier if cost_profile.instrument_type == "FUTURES" else 1.0
                if cost_profile.instrument_type == "FUTURES" and config.initial_margin_per_contract is not None:
                    required_margin = abs(resulting_net) * config.initial_margin_per_contract
                    if required_margin > cash + 1e-9:
                        rejects.append({"order_id": oid, "bar_ts": bar["bar_start"], "reason": "INSUFFICIENT_MARGIN",
                                        "detail": f"required={required_margin:.2f}, available={cash:.2f}"})
                        record["done"] = True
                        continue
                if config.max_gross_notional is not None and abs(resulting_net * fill_price * multiplier) > config.max_gross_notional:
                    rejects.append({"order_id": oid, "bar_ts": bar["bar_start"], "reason": "RISK_LIMIT",
                                    "detail": f"gross_notional={abs(resulting_net * fill_price * multiplier):.2f}"})
                    record["done"] = True
                    continue
                fill = {
                    "order_id": oid, "symbol": order.symbol, "market": order.market,
                    "contract": order.contract, "exec_ts": bar["bar_start"], "side": side,
                    "qty": qty, "fill_price": fill_price, "fee": fee, "tax": tax,
                    "signal_time": record['signal_time'], "signal_available_at": record['available'],
                    "order_time": record['order_time'],
                    "regulatory_fee": regulatory_fee,
                    "strategy_id": order.strategy_id, "experiment_id": order.experiment_id,
                    "session": bar["session"], "execution_assumption": "SAME_TIMESTAMP_BAR_OPEN" if bar["bar_start"] == record["available"] else "NEXT_ELIGIBLE_BAR_OPEN",
                }
                fill.update(cost_parts)
                fill['raw_price'] = raw_price
                fill['price_adjustment_cost'] = abs(Decimal(str(fill_price)) - Decimal(str(raw_price))) * Decimal(str(qty)) * Decimal(str(multiplier))
                monetary_scale = Decimal(str(qty)) * Decimal(str(multiplier))
                if cost_profile.equity_slippage_bps is not None:
                    slip_move = Decimal(str(raw_price)) * Decimal(str(cost_profile.equity_slippage_bps)) / Decimal('10000')
                else:
                    slip_move = Decimal(str(cost_profile.slippage_ticks)) * Decimal(str(cost_profile.tick_size))
                spread_move = Decimal(str(raw_price)) * Decimal(str(cost_profile.half_spread_bps or 0)) / Decimal('10000')
                fill['slippage_cost'] = slip_move * monetary_scale * cost_multiplier
                fill['spread_cost'] = spread_move * monetary_scale * cost_multiplier
                fill['tick_rounding_cost'] = fill['price_adjustment_cost'] - fill['slippage_cost'] - fill['spread_cost']
                fill['explicit_cost'] = fee + tax + regulatory_fee
                fill['commission_scope'] = cost_profile.fee_mode or 'LEGACY'
                fill['cost_profile_id'] = cost_profile.profile_id
                fill['cost_profile_version'] = cost_profile.version
                fill['cost_profile_sha256'] = cost_profile.sha256
                if config.temporal_policy is not None:
                    fill.update(temporal_policy_id=config.temporal_policy.policy_id,
                                temporal_policy_version=config.temporal_policy.version,
                                temporal_policy_sha256=config.temporal_policy.sha256,
                                temporal_assumption=config.temporal_policy.assumption,
                                source_timestamp=bar['source_timestamp'], source_timestamp_label='UNKNOWN',
                                modeled_available_at=record['signal_time'], signal_source_timestamp=record['signal_source_timestamp'],
                                candidate_modeled_available_at=bar['modeled_available_at'],
                                candidate_possible_open_latest=bar['possible_open_latest'],
                                execution_eligibility_scope='RESEARCH_ONLY')
                fills.append(fill)
                record["done"] = True
                record["signed_remaining"] = 0.0
                signed_qty = qty if side == "BUY" else -qty
                filled_key = (order.strategy_id, order.symbol, order.contract)
                filled_positions[filled_key] = filled_positions.get(filled_key, 0.0) + signed_qty
                order_events.append({"order_id": oid, "event_ts": bar["bar_start"],
                    "event": "ORDER_FILLED", "reason_code": "FULL_FILL", "side": side, "qty": qty})
                lots.setdefault(key, [])
                remaining = qty
                # Consume opposite lots FIFO; each pair closes only within the same contract.
                while remaining > 1e-12 and lots[key] and lots[key][0]["signed_qty"] * signed_qty < 0:
                    lot = lots[key][0]
                    matched = min(remaining, abs(lot["signed_qty"]))
                    matched_d = Decimal(str(matched))
                    qty_d = Decimal(str(qty))
                    entry_remaining_d = Decimal(str(abs(lot["signed_qty"])))
                    gross_d = ((Decimal(str(fill_price)) - Decimal(str(lot["entry_price"])))
                               * Decimal(str(math.copysign(matched, lot["signed_qty"])))
                               * Decimal(str(multiplier)))
                    entry_fee_alloc = lot["fee_remaining"] * matched_d / entry_remaining_d
                    exit_fee_alloc = fee * matched_d / qty_d
                    entry_tax_alloc = lot["tax_remaining"] * matched_d / entry_remaining_d
                    exit_tax_alloc = tax * matched_d / qty_d
                    entry_reg_alloc = lot["regulatory_fee_remaining"] * matched_d / entry_remaining_d
                    exit_reg_alloc = regulatory_fee * matched_d / qty_d
                    net_d = (gross_d - entry_fee_alloc - exit_fee_alloc - entry_tax_alloc - exit_tax_alloc
                             - entry_reg_alloc - exit_reg_alloc)
                    gross = float(gross_d)
                    if cost_profile.instrument_type == "FUTURES":
                        cash += gross - float(exit_fee_alloc) - float(exit_tax_alloc) - float(exit_reg_alloc)
                    else:
                        cash += math.copysign(fill_price * matched, lot["signed_qty"])
                    start_ts = lot["entry_ts"]
                    end_ts = bar["bar_start"]
                    exclusion = next((label for start, end, label in config.exclusion_windows if _ts(start, config.timestamp_timezone) < end_ts and _ts(end, config.timestamp_timezone) > start_ts), None)
                    trip = {
                        "symbol": order.symbol, "market": order.market, "contract": order.contract,
                        "entry_ts": start_ts, "exit_ts": end_ts, "side": "LONG" if lot["signed_qty"] > 0 else "SHORT",
                        "qty": matched, "entry_price": lot["entry_price"], "exit_price": fill_price,
                        "gross_pnl": _money(gross_d), "net_pnl": _money(net_d), "invalidated": bool(exclusion),
                        "invalidation_reason": exclusion, "strategy_id": lot["strategy_id"], "experiment_id": lot["experiment_id"],
                    }
                    (invalid if exclusion else trips).append(trip)
                    lot["signed_qty"] -= math.copysign(matched, lot["signed_qty"])
                    lot["fee_remaining"] -= entry_fee_alloc
                    lot["tax_remaining"] -= entry_tax_alloc
                    lot["regulatory_fee_remaining"] -= entry_reg_alloc
                    remaining -= matched
                    if abs(lot["signed_qty"]) < 1e-12:
                        lots[key].pop(0)
                if remaining > 1e-12:
                    open_signed = math.copysign(remaining, signed_qty)
                    if cost_profile.instrument_type == "FUTURES":
                        cash -= float((fee + tax + regulatory_fee) * Decimal(str(remaining)) / Decimal(str(qty)))
                    else:
                        cash -= open_signed * fill_price * remaining
                    lots[key].append({
                        "signed_qty": open_signed, "entry_price": fill_price, "entry_ts": bar["bar_start"],
                        "fee_remaining": fee * Decimal(str(remaining)) / Decimal(str(qty)), "tax_remaining": tax * Decimal(str(remaining)) / Decimal(str(qty)),
                        "regulatory_fee_remaining": regulatory_fee * Decimal(str(remaining)) / Decimal(str(qty)),
                        "strategy_id": order.strategy_id, "experiment_id": order.experiment_id,
                        "market": order.market, "symbol": order.symbol, "contract": order.contract,
                    })
                # Futures opening costs are charged at entry; equity costs are charged here as cash expenses.
                if cost_profile.instrument_type == "FUTURES":
                    opened = max(0.0, remaining)
                    cash -= float((fee + tax + regulatory_fee) * (Decimal("1") - Decimal(str(opened)) / Decimal(str(qty))))
                else:
                    cash -= float(fee + tax + regulatory_fee)

        for record in records:
            if not record["done"]:
                rejects.append({
                    "order_id": record["oid"], "reason": record["last_reason"],
                    "detail": "no eligible execution bar within TIF",
                })
                record["done"] = True

        # Mark-to-market the open lots with each final available close. No forced liquidation.
        marks = {(r["symbol"], r["contract"]): r for _, r in frame.groupby(["symbol", "contract"], sort=False).tail(1).iterrows()}
        open_rows = []
        for key, key_lots in lots.items():
            mark = marks.get(key)
            if mark is None:
                raise ValueError(f"no mark available for open position {key}")
            for lot in key_lots:
                multiplier = cost_profile.contract_multiplier if cost_profile.instrument_type == "FUTURES" else 1.0
                unrealized = (float(mark["close"]) - lot["entry_price"]) * lot["signed_qty"] * multiplier
                margin = abs(lot["signed_qty"]) * (config.initial_margin_per_contract or 0.0) if cost_profile.instrument_type == "FUTURES" else 0.0
                open_rows.append({
                    "symbol": lot["symbol"], "contract": lot["contract"], "side": "LONG" if lot["signed_qty"] > 0 else "SHORT",
                    "qty": abs(lot["signed_qty"]), "entry_price": lot["entry_price"], "mark_price": float(mark["close"]),
                    "unrealized_pnl": _money(unrealized), "margin_requirement": _money(margin),
                    "entry_ts": lot["entry_ts"], "strategy_id": lot["strategy_id"], "experiment_id": lot["experiment_id"],
                })
        open_df = pd.DataFrame(open_rows)
        # Rebuild account marks at every bar close from fills and FIFO lots.
        curve = []
        mark_lots: dict[tuple[str, str], list[dict[str, float]]] = {}
        mark_cash = float(config.initial_cash)
        fills_by_time: dict[pd.Timestamp, list[dict[str, Any]]] = {}
        for item in fills:
            fills_by_time.setdefault(pd.Timestamp(item["exec_ts"]), []).append(item)
        for _, bar in frame.iterrows():
            k = (bar["symbol"], bar["contract"])
            for item in fills_by_time.pop(pd.Timestamp(bar["bar_start"]), []):
                signed = item["qty"] if item["side"] == "BUY" else -item["qty"]
                queue = mark_lots.setdefault(k, [])
                remaining = float(item["qty"])
                if cost_profile.instrument_type == "EQUITY":
                    mark_cash -= signed * item["fill_price"]
                while remaining > 1e-12 and queue and queue[0]["signed_qty"] * signed < 0:
                    lot = queue[0]
                    matched = min(remaining, abs(lot["signed_qty"]))
                    gross = ((item["fill_price"] - lot["entry_price"])
                             * math.copysign(matched, lot["signed_qty"])
                             * (cost_profile.contract_multiplier if cost_profile.instrument_type == "FUTURES" else 1.0))
                    if cost_profile.instrument_type == "FUTURES":
                        mark_cash += gross
                    lot["signed_qty"] -= math.copysign(matched, lot["signed_qty"])
                    remaining -= matched
                    if abs(lot["signed_qty"]) < 1e-12:
                        queue.pop(0)
                if remaining > 1e-12:
                    queue.append({"signed_qty": math.copysign(remaining, signed), "entry_price": item["fill_price"]})
                mark_cash -= float(item["fee"] + item["tax"] + item["regulatory_fee"])
            unrealized = 0.0
            market_value = 0.0
            margin = 0.0
            for (symbol, contract), queue in mark_lots.items():
                matching_bar = frame[(frame["symbol"] == symbol) & (frame["contract"] == contract)
                                     & (frame["bar_start"] <= bar["bar_start"])]
                if matching_bar.empty:
                    continue
                mark_price = float(matching_bar.iloc[-1]["close"])
                for lot in queue:
                    signed_qty = lot["signed_qty"]
                    unrealized += ((mark_price - lot["entry_price"]) * signed_qty
                                   * (cost_profile.contract_multiplier if cost_profile.instrument_type == "FUTURES" else 1.0))
                    market_value += mark_price * signed_qty
                    if cost_profile.instrument_type == "FUTURES":
                        margin += abs(signed_qty) * (config.initial_margin_per_contract or 0.0)
            curve.append({"timestamp": bar["bar_end"], "cash": _money(mark_cash),
                          "realized_net_pnl": _money(mark_cash - config.initial_cash - unrealized) if cost_profile.instrument_type == "FUTURES" else _money(mark_cash - config.initial_cash + market_value),
                          "unrealized_pnl": _money(unrealized), "margin_requirement": _money(margin),
                          "equity": _money(mark_cash + unrealized if cost_profile.instrument_type == "FUTURES" else mark_cash + market_value)})
        eq = pd.DataFrame(curve)
        profile_sha = cost_profile.sha256
        provenance = {
            "engine_name": "market-research-core.ExecutionEngine", "engine_git_sha": config.core_commit_sha,
            "engine_version": "2.2.0", "execution_convention": "oracle-execution-convention-v1",
            "dataset_snapshot_id": config.dataset_snapshot_id, "dataset_producer_sha": config.dataset_producer_sha,
            "dataset_verdict": config.dataset_verdict, "cost_profile_id": cost_profile.profile_id,
            "cost_profile_version": cost_profile.version, "cost_profile_sha256": profile_sha, "cost_profile_approval": cost_profile.approval_status,
            "cost_profile_components": cost_profile.components,
            "exclusions_applied": [label for _, _, label in config.exclusion_windows],
            "strategy_ids": sorted({o.strategy_id for o in orders}), "experiment_ids": sorted({o.experiment_id for o in orders}),
            "spec_sha256": config.spec_sha256, "parameters": config.parameters,
            "cost_multiplier": config.cost_multiplier, "seed": config.seed,
            "window_start": config.window_start, "window_end": config.window_end,
            "bar_label": config.bar_label, "session_semantics": config.session_semantics,
            "timestamp_timezone": config.timestamp_timezone, "available_at_policy": config.available_at_policy,
            "assumptions": assumptions,
            "permitted_usage_scope": config.permitted_usage_scope,
        }
        if config.temporal_policy is not None:
            provenance.update(temporal_policy_id=config.temporal_policy.policy_id,
                              temporal_policy_version=config.temporal_policy.version,
                              temporal_policy_sha256=config.temporal_policy.sha256,
                              temporal_policy=asdict(config.temporal_policy), source_timestamp_label='UNKNOWN',
                              modeled_available_at_rule='LATEST_START_END_COMPLETION',
                              execution_eligibility_scope='RESEARCH_ONLY')
        provenance['cost_configuration'] = cost_profile.configuration_metadata
        provenance['fee_mode'] = cost_profile.fee_mode
        formal_eligible = (
            config.permitted_usage_scope == "BACKTEST"
            and config.available_at_policy == "OBSERVED"
            and cost_profile.approval_status == "APPROVED"
            and all(basis in {"OFFICIAL", "BROKER_ACTUAL"} for basis in cost_profile.component_basis.values())
            and all(status == "APPROVED" for status in (
                cost_profile.component_approval_status.values() or [cost_profile.approval_status]
            ))
            and config.dataset_verdict in {"GO", "SCOPED_GO"}
            and bool(re.fullmatch(r"[0-9a-fA-F]{40}", config.core_commit_sha))
            and bool(config.dataset_snapshot_id and config.dataset_snapshot_id != "UNKNOWN")
            and bool(re.fullmatch(r"[0-9a-fA-F]{40}", config.dataset_producer_sha))
            and bool(re.fullmatch(r"[0-9a-fA-F]{64}", config.spec_sha256))
            and not invalid
        )
        return ExecutionResult(
            fills=pd.DataFrame(fills), round_trips=pd.DataFrame(trips), rejections=pd.DataFrame(rejects),
            equity_curve=eq, open_positions=open_df, invalidated_round_trips=pd.DataFrame(invalid),
            order_events=pd.DataFrame(order_events),
            provenance=provenance, status="RESEARCH_ONLY" if not formal_eligible else "FORMAL_ELIGIBLE",
        )
