from __future__ import annotations

from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version

import pandas as pd

from .errors import BacktestRejected
from .models import BacktestAssumptions, BacktestResult, UnifiedBars

REQUIRED = {"timestamp", "open", "high", "low", "close", "volume", "available_at",
            "data_exists", "research_eligible", "backtest_eligible", "execution_eligible"}


def _code_version() -> str:
    try:
        return version("market-research-core")
    except PackageNotFoundError:
        return "0.2.0+source"


class BacktestEngine:
    """Deterministic long-only SMA integration probe, not a strategy claim."""

    def run_sma(self, loaded: UnifiedBars, assumptions: BacktestAssumptions = BacktestAssumptions()) -> BacktestResult:
        # Adapters may intentionally retain timestamp both as a column and as
        # their native index.  Normalize the row index before column sorting so
        # pandas cannot interpret the label as two different sort keys.
        frame = loaded.frame.copy().reset_index(drop=True).sort_values("timestamp").reset_index(drop=True)
        missing = sorted(REQUIRED - set(frame.columns))
        if missing:
            raise BacktestRejected(f"Backtest data contract columns missing: {', '.join(missing)}")
        if assumptions.fast_window <= 0 or assumptions.slow_window <= assumptions.fast_window:
            raise BacktestRejected("SMA windows must satisfy 0 < fast < slow")
        if assumptions.performance_claim:
            if loaded.provenance.market in {"IBKR", "TXF"} and not assumptions.contract_multiplier:
                raise BacktestRejected("A futures performance claim requires an explicit contract multiplier")
            raise BacktestRejected("Phase 2 integration probes may not be labelled formal performance")

        frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
        frame["timestamp_cmp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame["fast"] = frame["close"].rolling(assumptions.fast_window, min_periods=assumptions.fast_window).mean()
        frame["slow"] = frame["close"].rolling(assumptions.slow_window, min_periods=assumptions.slow_window).mean()
        frame["desired"] = (frame.fast > frame.slow).fillna(False).astype(int)
        frame["decision"] = frame.desired.diff().fillna(frame.desired)

        cash, units, entry_cost = assumptions.initial_cash, 0.0, 0.0
        trades, rejects, curve = [], [], []
        multiplier = assumptions.contract_multiplier or 1.0
        last_exec = -1
        for signal_idx in frame.index[frame.decision.ne(0)]:
            signal = frame.loc[signal_idx]
            candidates = frame.loc[(frame.index > signal_idx) & (frame.timestamp_cmp >= signal.available_at)]
            if last_exec >= 0:
                candidates = candidates.loc[candidates.index > last_exec]
            rejected = candidates.loc[~candidates.execution_eligible.astype(bool)]
            for r in rejected.itertuples():
                rejects.append({"signal_timestamp": signal.timestamp, "bar_timestamp": r.timestamp,
                    "reason": "EXECUTION_INELIGIBLE", "data_exists": bool(r.data_exists),
                    "research_eligible": bool(r.research_eligible), "backtest_eligible": bool(r.backtest_eligible)})
            eligible = candidates.loc[candidates.execution_eligible.astype(bool)]
            if eligible.empty:
                rejects.append({"signal_timestamp": signal.timestamp, "bar_timestamp": pd.NaT,
                    "reason": "NO_LATER_ELIGIBLE_BAR", "data_exists": False,
                    "research_eligible": False, "backtest_eligible": False})
                continue
            i = int(eligible.index[0])
            bar = frame.loc[i]
            side = "BUY" if signal.decision > 0 else "SELL"
            if side == "BUY" and units == 0:
                px = float(bar.open) * (1 + assumptions.slippage_bps / 10_000)
                gross = assumptions.unit_size * multiplier * px
                fee = gross * assumptions.fee_rate
                if gross + fee > cash:
                    rejects.append({"signal_timestamp": signal.timestamp, "bar_timestamp": bar.timestamp,
                        "reason": "INSUFFICIENT_CASH", "data_exists": True,
                        "research_eligible": True, "backtest_eligible": True})
                    continue
                cash -= gross + fee
                units = assumptions.unit_size
                entry_cost = gross + fee
                trades.append({"timestamp": bar.timestamp, "signal_timestamp": signal.timestamp, "side": side,
                    "price": px, "units": units, "fee": fee, "tax": 0.0, "cash_after": cash,
                    "position_after": units})
                last_exec = i
            elif side == "SELL" and units > 0:
                px = float(bar.open) * (1 - assumptions.slippage_bps / 10_000)
                gross = units * multiplier * px
                fee, tax = gross * assumptions.fee_rate, gross * assumptions.sell_tax_rate
                cash += gross - fee - tax
                trades.append({"timestamp": bar.timestamp, "signal_timestamp": signal.timestamp, "side": side,
                    "price": px, "units": units, "fee": fee, "tax": tax, "cash_after": cash,
                    "position_after": 0.0, "realized_pnl": gross - fee - tax - entry_cost})
                units, entry_cost, last_exec = 0.0, 0.0, i

        trade_map = {pd.Timestamp(t["timestamp"]): t for t in trades}
        curve_cash, curve_units = assumptions.initial_cash, 0.0
        for row in frame.itertuples():
            event = trade_map.get(pd.Timestamp(row.timestamp))
            if event is not None:
                curve_cash, curve_units = float(event["cash_after"]), float(event["position_after"])
            equity = curve_cash + curve_units * multiplier * float(row.close)
            curve.append({"timestamp": row.timestamp, "cash": curve_cash, "position": curve_units, "equity": equity})
        equity_curve = pd.DataFrame(curve)
        if equity_curve.empty:
            raise BacktestRejected("No bars available for backtest")
        peak = equity_curve.equity.cummax()
        max_drawdown = float((equity_curve.equity / peak - 1).min())
        total_return = float(equity_curve.equity.iloc[-1] / assumptions.initial_cash - 1)
        provenance = {
            "engine_version": _code_version(),
            "strategy": "sma_crossover_previous_signal_next_eligible_bar",
            "strategy_parameters": asdict(assumptions),
            "data": asdict(loaded.provenance),
            "performance_status": "RESEARCH_INTEGRATION_DEMO",
            "lookahead_rule": "signal only after available_at; execution on a later eligible real bar",
        }
        return BacktestResult(pd.DataFrame(trades), equity_curve, total_return, max_drawdown,
            int(len(pd.DataFrame(trades).loc[lambda x: x.get("side", pd.Series(dtype=str)).eq("SELL")])) if trades else 0,
            pd.DataFrame(rejects), provenance)
