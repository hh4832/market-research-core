"""TSM's explicit NYSE session mask. Session labels never imply fill eligibility."""
from __future__ import annotations

from importlib.metadata import version
from typing import Literal

import pandas as pd


NYSE_CALENDAR_VERSION = "5.5.0"
TIMESTAMP_LABEL = Literal["START", "END", "UNKNOWN"]
AVAILABILITY_POLICY = Literal["OBSERVED", "BAR_END_STRICT_LATER", "UNKNOWN"]


def _fail_closed(frame: pd.DataFrame, reason: str) -> pd.DataFrame:
    out = frame.copy()
    for column in ("bar_start", "bar_end", "available_at", "session_open", "session_close"):
        out[column] = pd.NaT
    out["session"] = "UNKNOWN"
    out["timestamp_label"] = "UNKNOWN"
    out["available_at_policy"] = "UNKNOWN"
    out["data_exists"] = True
    out["research_eligible"] = True
    out["backtest_eligible"] = True
    out["buy_execution_eligible"] = False
    out["sell_execution_eligible"] = False
    out["execution_eligible"] = False
    out["execution_rejection_reason"] = reason
    out["buy_execution_rejection_reason"] = reason
    out["sell_execution_rejection_reason"] = reason
    return out


def apply_tsm_session_mask(
    frame: pd.DataFrame,
    *,
    timestamp_label: TIMESTAMP_LABEL = "UNKNOWN",
    availability_policy: AVAILABILITY_POLICY = "UNKNOWN",
) -> pd.DataFrame:
    """Normalize one-minute TSM labels and NYSE sessions without approving fills.

    BAR_END_STRICT_LATER models availability at bar_end for research only. It is
    not a claim about IBKR's actual publication latency. The execution engine
    excludes a candidate whose bar_start equals available_at unless an explicit
    same-time assumption is used; that assumption is forbidden with this policy.
    """
    if timestamp_label == "UNKNOWN":
        return _fail_closed(frame, "TIMESTAMP_LABEL_OR_SESSION_UNKNOWN")
    if timestamp_label not in {"START", "END"}:
        raise ValueError("timestamp_label must be START, END, or UNKNOWN")
    if availability_policy not in {"OBSERVED", "BAR_END_STRICT_LATER", "UNKNOWN"}:
        raise ValueError("unsupported availability_policy")
    if availability_policy == "UNKNOWN":
        return _fail_closed(frame, "AVAILABLE_AT_POLICY_UNKNOWN")

    out = frame.copy()
    ts_column = next((c for c in ("timestamp", "timestamp_utc", "timestamp_utc_source") if c in out), None)
    if ts_column is None:
        raise ValueError("TSM source timestamp column is missing")
    source_ts = pd.to_datetime(out[ts_column], utc=True, errors="raise")
    duration = pd.Timedelta(minutes=1)
    if timestamp_label == "START":
        out["bar_start"] = source_ts
        out["bar_end"] = source_ts + duration
    else:
        out["bar_end"] = source_ts
        out["bar_start"] = source_ts - duration
    out["timestamp_label"] = timestamp_label
    out["available_at_policy"] = availability_policy

    if availability_policy == "BAR_END_STRICT_LATER":
        out["available_at"] = out["bar_end"]
    else:
        if "available_at" not in out or out["available_at"].isna().any():
            return _fail_closed(frame, "OBSERVED_AVAILABLE_AT_MISSING")
        out["available_at"] = pd.to_datetime(out["available_at"], utc=True, errors="raise")
        if (out["available_at"] < out["bar_end"]).any():
            raise ValueError("observed available_at cannot precede bar_end")

    if out.empty:
        out["session"] = pd.Series(dtype="object")
        out["session_open"] = pd.Series(dtype="datetime64[ns, UTC]")
        out["session_close"] = pd.Series(dtype="datetime64[ns, UTC]")
        return out

    actual_version = version("pandas-market-calendars")
    if actual_version != NYSE_CALENDAR_VERSION:
        raise RuntimeError(
            f"NYSE calendar version mismatch: expected {NYSE_CALENDAR_VERSION}, found {actual_version}"
        )
    import pandas_market_calendars as mcal

    ny_dates = pd.DatetimeIndex(out["bar_start"]).tz_convert("America/New_York").date
    start_date, end_date = min(ny_dates).isoformat(), max(ny_dates).isoformat()
    schedule = mcal.get_calendar("NYSE").schedule(start_date=start_date, end_date=end_date)
    schedules = {
        idx.date().isoformat(): (pd.Timestamp(row["market_open"]).tz_convert("UTC"),
                                 pd.Timestamp(row["market_close"]).tz_convert("UTC"))
        for idx, row in schedule.iterrows()
    }

    sessions: list[str] = []
    opens: list[object] = []
    closes: list[object] = []
    for start, end, ny_date in zip(out["bar_start"], out["bar_end"], ny_dates):
        entry = schedules.get(ny_date.isoformat())
        if entry is None:
            sessions.append("OUTSIDE_SESSION")
            opens.append(pd.NaT)
            closes.append(pd.NaT)
            continue
        market_open, market_close = entry
        local_midnight = pd.Timestamp(ny_date).tz_localize("America/New_York")
        pre_open = (local_midnight + pd.Timedelta(hours=4)).tz_convert("UTC")
        post_close = (local_midnight + pd.Timedelta(hours=20)).tz_convert("UTC")
        if start >= market_open and end <= market_close:
            session = "RTH"
        elif start >= pre_open and end <= market_open:
            session = "PRE_MARKET"
        elif start >= market_close and end <= post_close:
            session = "AFTER_HOURS"
        else:
            session = "OUTSIDE_SESSION"
        sessions.append(session)
        opens.append(market_open)
        closes.append(market_close)

    out["session"] = sessions
    out["session_open"] = pd.to_datetime(opens, utc=True)
    out["session_close"] = pd.to_datetime(closes, utc=True)

    # Session classification is separate from tradability. No side becomes
    # eligible until an independently approved market execution policy exists.
    out["data_exists"] = True
    out["research_eligible"] = True
    out["backtest_eligible"] = True
    out["buy_execution_eligible"] = False
    out["sell_execution_eligible"] = False
    out["execution_eligible"] = False
    out["execution_rejection_reason"] = "EXECUTION_POLICY_NOT_APPROVED"
    out["buy_execution_rejection_reason"] = "EXECUTION_POLICY_NOT_APPROVED"
    out["sell_execution_rejection_reason"] = "EXECUTION_POLICY_NOT_APPROVED"
    return out
