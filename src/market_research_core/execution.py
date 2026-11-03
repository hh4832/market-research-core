"""Point-in-time, signal-driven execution contract.

The engine consumes normalized bars and explicit orders. It never generates signals.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Any, Iterable, Literal

import pandas as pd

from .costs import CostProfile, calculate_costs, adverse_fill_price


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

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not math.isfinite(self.cost_multiplier) or self.cost_multiplier < 0:
            raise ValueError("cost_multiplier must be finite and non-negative")
        if self.bar_label == "UNKNOWN" or self.session_semantics == "UNKNOWN":
            raise ValueError("formal execution is blocked when bar label or session semantics are UNKNOWN")
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
    provenance: dict[str, Any]
    status: str


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


def _money(value: float) -> float:
    return float(round(value + 1e-10, 2))


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
        frame = bars.copy()
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
        if frame["session"].astype(str).str.upper().eq("UNKNOWN").any():
            raise ValueError("formal execution is blocked for UNKNOWN session rows")

        orders = sorted(list(orders), key=lambda o: (_ts(o.order_time, config.timestamp_timezone), o.order_id or ""))
        fills: list[dict[str, Any]] = []
        rejects: list[dict[str, Any]] = []
        trips: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        lots: dict[tuple[str, str], list[dict[str, Any]]] = {}
        cash = float(config.initial_cash)
        order_outcomes: dict[str, str] = {}
        target_positions: dict[tuple[str, str, str], float] = {}
        assumptions = ["FIFO lot matching", "orders execute at bar open for market orders", "end-of-data positions remain open"]

        for order_idx, order in enumerate(orders):
            oid = order.order_id or f"order-{order_idx + 1}"
            signal_time = _ts(order.signal_time, config.timestamp_timezone)
            available = _ts(order.signal_available_at, config.timestamp_timezone)
            order_time = _ts(order.order_time, config.timestamp_timezone)
            if available < signal_time or order_time < available:
                rejects.append({"order_id": oid, "reason": "PIT_VIOLATION", "detail": "signal_available_at/order_time precedes prior information"})
                order_outcomes[oid] = "REJECTED"
                continue
            if order.market in ("TXF", "IBKR_FUTURES") and config.initial_margin_per_contract is None:
                raise ValueError("TXF execution requires explicit initial_margin_per_contract")
            if order.market in ("TXF", "IBKR_FUTURES") and cost_profile.instrument_type != "FUTURES":
                raise ValueError("TXF requires a FUTURES CostProfile")
            if order.market not in ("TXF", "IBKR_FUTURES") and cost_profile.instrument_type != "EQUITY":
                raise ValueError("equity market requires an EQUITY CostProfile")

            key = (order.symbol, order.contract)
            target_key = None
            if order.target_position is not None:
                target_key = (order.strategy_id, order.symbol, order.contract)
                current_target = target_positions.get(target_key, 0.0)
                delta = order.target_position - current_target
                if abs(delta) < 1e-12:
                    order_outcomes[oid] = "NOOP"
                    continue
                side = "BUY" if delta > 0 else "SELL"
                qty = abs(delta)
            else:
                side, qty = order.side, float(order.quantity)
            if side != order.side:
                rejects.append({"order_id": oid, "reason": "TARGET_SIDE_MISMATCH", "detail": f"target requires {side}"})
                order_outcomes[oid] = "REJECTED"
                continue

            candidates = frame[
                (frame["symbol"] == order.symbol)
                & (frame["market"] == order.market)
                & (frame["contract"] == order.contract)
                & (frame["bar_start"] >= available)
                & (frame["bar_start"] >= order_time)
                & (frame["bar_end"] > signal_time)
            ]
            if not config.same_timestamp_open_assumption:
                candidates = candidates[candidates["bar_start"] > max(available, order_time)]
            chosen = None
            reason = "UNFILLED_AT_END_OF_DATA"
            if order.time_in_force == "NEXT_BAR":
                candidates = candidates.iloc[:1]
            elif not candidates.empty:
                first_session_open = candidates.iloc[0]["session_open"]
                candidates = candidates[candidates["session_open"] == first_session_open]
            for _, bar in candidates.iterrows():
                if order.time_in_force == "DAY" and bar["bar_start"] >= bar["session_close"]:
                    reason = "ORDER_EXPIRED"
                    break
                if bar["bar_start"] < bar["session_open"] or bar["bar_start"] >= bar["session_close"]:
                    reason = "OUTSIDE_SESSION"
                    continue
                if not bool(bar["data_exists"]) or not bool(bar["backtest_eligible"]):
                    reason = "BAR_NOT_ELIGIBLE"
                    continue
                if float(bar["volume"]) <= 0:
                    reason = "ZERO_VOLUME_BAR"
                    continue
                eligible_col = "buy_execution_eligible" if side == "BUY" else "sell_execution_eligible"
                if not bool(bar[eligible_col]):
                    reason_col = f"{side.lower()}_execution_rejection_reason"
                    reason_value = bar.get(reason_col, bar["execution_rejection_reason"])
                    reason = str(reason_value) if pd.notna(reason_value) and str(reason_value) else f"{side}_NOT_ELIGIBLE"
                    continue
                exclusion = _in_window(bar["bar_start"], config.exclusion_windows, config.timestamp_timezone)
                if exclusion:
                    reason = "BAR_IN_EXCLUSION_WINDOW"
                    break
                if order.order_type == "LIMIT":
                    if side == "BUY" and float(bar["low"]) > float(order.limit_price):
                        reason = "LIMIT_NOT_TOUCHED"
                        continue
                    if side == "SELL" and float(bar["high"]) < float(order.limit_price):
                        reason = "LIMIT_NOT_TOUCHED"
                        continue
                    raw_price = min(float(bar["open"]), float(order.limit_price)) if side == "BUY" else max(float(bar["open"]), float(order.limit_price))
                else:
                    raw_price = float(bar["open"])
                chosen = (bar, raw_price)
                break
            if chosen is None:
                rejects.append({"order_id": oid, "reason": reason, "detail": "no eligible execution bar within TIF"})
                order_outcomes[oid] = "REJECTED" if reason not in ("UNFILLED_AT_END_OF_DATA", "ORDER_EXPIRED") else reason
                continue

            bar, raw_price = chosen
            fill_price = adverse_fill_price(raw_price, side, cost_profile, config.cost_multiplier)
            if order.order_type == "LIMIT":
                fill_price = min(fill_price, float(order.limit_price)) if side == "BUY" else max(fill_price, float(order.limit_price))
            fee, tax = calculate_costs(fill_price, qty, side, cost_profile)
            fee, tax = _money(fee * config.cost_multiplier), _money(tax * config.cost_multiplier)
            signed_delta = qty if side == "BUY" else -qty
            current_net = sum(lot["signed_qty"] for lot in lots.get(key, []))
            resulting_net = current_net + signed_delta
            multiplier = cost_profile.contract_multiplier if cost_profile.instrument_type == "FUTURES" else 1.0
            if cost_profile.instrument_type == "FUTURES" and config.initial_margin_per_contract is not None:
                required_margin = abs(resulting_net) * config.initial_margin_per_contract
                if required_margin > cash + 1e-9:
                    rejects.append({"order_id": oid, "reason": "INSUFFICIENT_MARGIN",
                                    "detail": f"required={required_margin:.2f}, available={cash:.2f}"})
                    order_outcomes[oid] = "REJECTED"
                    continue
            if config.max_gross_notional is not None and abs(resulting_net * fill_price * multiplier) > config.max_gross_notional:
                rejects.append({"order_id": oid, "reason": "RISK_LIMIT",
                                "detail": f"gross_notional={abs(resulting_net * fill_price * multiplier):.2f}"})
                order_outcomes[oid] = "REJECTED"
                continue
            fill = {
                "order_id": oid, "symbol": order.symbol, "market": order.market,
                "contract": order.contract, "exec_ts": bar["bar_start"], "side": side,
                "qty": qty, "fill_price": fill_price, "fee": fee, "tax": tax,
                "strategy_id": order.strategy_id, "experiment_id": order.experiment_id,
                "session": bar["session"], "execution_assumption": "SAME_TIMESTAMP_BAR_OPEN" if bar["bar_start"] == available else "NEXT_ELIGIBLE_BAR_OPEN",
            }
            fills.append(fill)
            order_outcomes[oid] = "FILLED"
            if target_key is not None:
                target_positions[target_key] = float(order.target_position)

            multiplier = cost_profile.contract_multiplier if cost_profile.instrument_type == "FUTURES" else 1.0
            signed_qty = qty if side == "BUY" else -qty
            lots.setdefault(key, [])
            remaining = qty
            # Consume opposite lots FIFO; each pair closes only within the same contract.
            while remaining > 1e-12 and lots[key] and lots[key][0]["signed_qty"] * signed_qty < 0:
                lot = lots[key][0]
                matched = min(remaining, abs(lot["signed_qty"]))
                gross = (fill_price - lot["entry_price"]) * math.copysign(matched, lot["signed_qty"]) * multiplier
                entry_fee_alloc = lot["fee_remaining"] * matched / abs(lot["signed_qty"])
                exit_fee_alloc = fee * matched / qty
                entry_tax_alloc = lot["tax_remaining"] * matched / abs(lot["signed_qty"])
                exit_tax_alloc = tax * matched / qty
                net = gross - entry_fee_alloc - exit_fee_alloc - entry_tax_alloc - exit_tax_alloc
                if cost_profile.instrument_type == "FUTURES":
                    cash += gross - exit_fee_alloc - exit_tax_alloc
                else:
                    cash += math.copysign(fill_price * matched, lot["signed_qty"])
                start_ts = lot["entry_ts"]
                end_ts = bar["bar_start"]
                exclusion = next((label for start, end, label in config.exclusion_windows if _ts(start, config.timestamp_timezone) < end_ts and _ts(end, config.timestamp_timezone) > start_ts), None)
                trip = {
                    "symbol": order.symbol, "market": order.market, "contract": order.contract,
                    "entry_ts": start_ts, "exit_ts": end_ts, "side": "LONG" if lot["signed_qty"] > 0 else "SHORT",
                    "qty": matched, "entry_price": lot["entry_price"], "exit_price": fill_price,
                    "gross_pnl": _money(gross), "net_pnl": _money(net), "invalidated": bool(exclusion),
                    "invalidation_reason": exclusion, "strategy_id": lot["strategy_id"], "experiment_id": lot["experiment_id"],
                }
                (invalid if exclusion else trips).append(trip)
                lot["signed_qty"] -= math.copysign(matched, lot["signed_qty"])
                lot["fee_remaining"] -= entry_fee_alloc
                lot["tax_remaining"] -= entry_tax_alloc
                remaining -= matched
                if abs(lot["signed_qty"]) < 1e-12:
                    lots[key].pop(0)
            if remaining > 1e-12:
                open_signed = math.copysign(remaining, signed_qty)
                if cost_profile.instrument_type == "FUTURES":
                    cash -= fee * remaining / qty + tax * remaining / qty
                else:
                    cash -= open_signed * fill_price * remaining
                lots[key].append({
                    "signed_qty": open_signed, "entry_price": fill_price, "entry_ts": bar["bar_start"],
                    "fee_remaining": fee * remaining / qty, "tax_remaining": tax * remaining / qty,
                    "strategy_id": order.strategy_id, "experiment_id": order.experiment_id,
                    "market": order.market, "symbol": order.symbol, "contract": order.contract,
                })
            # Futures opening costs are charged at entry; equity costs are charged here as cash expenses.
            if cost_profile.instrument_type == "FUTURES":
                # Opening fee was charged above; avoid double charging when the fill only closes.
                opened = max(0.0, remaining)
                cash -= (fee + tax) * (1.0 - opened / qty)
            else:
                cash -= fee + tax

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
                mark_cash -= item["fee"] + item["tax"]
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
            "engine_version": "2.1.0", "execution_convention": "oracle-execution-convention-v1",
            "dataset_snapshot_id": config.dataset_snapshot_id, "dataset_producer_sha": config.dataset_producer_sha,
            "dataset_verdict": config.dataset_verdict, "cost_profile_id": cost_profile.profile_id,
            "cost_profile_sha256": profile_sha, "cost_profile_approval": cost_profile.approval_status,
            "exclusions_applied": [label for _, _, label in config.exclusion_windows],
            "strategy_ids": sorted({o.strategy_id for o in orders}), "experiment_ids": sorted({o.experiment_id for o in orders}),
            "spec_sha256": config.spec_sha256, "parameters": config.parameters,
            "cost_multiplier": config.cost_multiplier, "seed": config.seed,
            "window_start": config.window_start, "window_end": config.window_end,
            "bar_label": config.bar_label, "session_semantics": config.session_semantics,
            "timestamp_timezone": config.timestamp_timezone, "assumptions": assumptions,
            "permitted_usage_scope": config.permitted_usage_scope,
        }
        formal_eligible = (
            config.permitted_usage_scope == "BACKTEST"
            and cost_profile.approval_status == "APPROVED"
            and all(basis != "ASSUMED" for basis in cost_profile.component_basis.values())
            and config.dataset_verdict in {"GO", "SCOPED_GO"}
            and config.core_commit_sha != "UNKNOWN"
            and config.dataset_snapshot_id != "UNKNOWN"
            and config.dataset_producer_sha != "UNKNOWN"
            and config.spec_sha256 != "UNKNOWN"
            and not invalid
        )
        return ExecutionResult(
            fills=pd.DataFrame(fills), round_trips=pd.DataFrame(trips), rejections=pd.DataFrame(rejects),
            equity_curve=eq, open_positions=open_df, invalidated_round_trips=pd.DataFrame(invalid),
            provenance=provenance, status="RESEARCH_ONLY" if not formal_eligible else "FORMAL_ELIGIBLE",
        )
