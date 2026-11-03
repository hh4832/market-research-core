"""Bounded, read-only timestamp and closing-auction audit for the pinned TSM snapshot.

The audit reads only timestamp_utc and OHLCV columns for seven predeclared
America/New_York calendar dates. It reports both start-label and end-label
session hypotheses; it never declares either convention confirmed from bar
counts alone. Trade-condition/auction attribution remains unknown from OHLCV.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd


SNAPSHOT_ID = "20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL"
PRODUCER_SHA = "06359faf7ef4b8ff702a7ab1a6d07521d3b68f28"
DRIVE_FILE_ID = "1B_2WyFRHXUzwg3C1BR7GLApGl722Ezli"
SAMPLE_DATES = (
    "2016-10-20", "2016-10-21", "2016-10-25", "2016-10-28",
    "2016-11-04", "2016-11-07", "2016-11-08",
)
OHLCV = ("open", "high", "low", "close", "volume")
NY = "America/New_York"
AUCTION_CHECK_TIMES = ("15:58", "15:59", "16:00", "16:01", "16:02")


def _calendar_schedule(dates: Iterable[str]) -> pd.DataFrame:
    import pandas_market_calendars as mcal

    dates = tuple(dates)
    if not dates:
        raise ValueError("at least one predeclared date is required")
    return mcal.get_calendar("NYSE").schedule(
        start_date=min(dates), end_date=max(dates)
    )


def _format_pair(value: pd.Timestamp | None) -> tuple[str | None, str | None]:
    if value is None or pd.isna(value):
        return None, None
    ts = pd.Timestamp(value).tz_convert("UTC")
    return ts.isoformat(), ts.tz_convert(NY).isoformat()


def analyze_bars(
    frame: pd.DataFrame,
    *,
    dates: Iterable[str] = SAMPLE_DATES,
    schedule: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Summarize only the supplied rows without deciding the label convention."""
    dates = tuple(dates)
    required = {"timestamp_utc", *OHLCV}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"bounded audit missing columns: {sorted(missing)}")
    bars = frame[list(required)].copy()
    bars["timestamp_utc"] = pd.to_datetime(bars["timestamp_utc"], utc=True, errors="raise")
    if bars["timestamp_utc"].duplicated().any():
        raise ValueError("duplicate timestamp_utc rows in bounded sample")
    bars = bars.sort_values("timestamp_utc").reset_index(drop=True)
    bars["timestamp_ny"] = bars["timestamp_utc"].dt.tz_convert(NY)
    bars["date_ny"] = bars["timestamp_ny"].dt.strftime("%Y-%m-%d")
    bars["time_ny"] = bars["timestamp_ny"].dt.strftime("%H:%M")
    schedule = schedule if schedule is not None else _calendar_schedule(dates)
    schedule_by_day = {idx.strftime("%Y-%m-%d"): row for idx, row in schedule.iterrows()}

    evidence_rows: list[dict] = []
    auction_rows: list[dict] = []
    all_start_matches: list[bool] = []
    all_end_matches: list[bool] = []

    for day in dates:
        day_bars = bars.loc[bars["date_ny"].eq(day)].copy()
        session = schedule_by_day.get(day)
        base = {
            "snapshot_id": SNAPSHOT_ID,
            "producer_sha": PRODUCER_SHA,
            "date_ny": day,
            "rows_on_ny_date": int(len(day_bars)),
            "first_source_timestamp_utc": None,
            "first_source_timestamp_ny": None,
            "last_source_timestamp_utc": None,
            "last_source_timestamp_ny": None,
            "has_premarket_timestamp_0400_to_open": False,
            "premarket_timestamp_rows": 0,
            "has_afterhours_timestamp_close_to_2000": False,
            "afterhours_timestamp_rows": 0,
            "session_status": "NO_DATA",
            "scheduled_rth_minutes": None,
            "normal_390_minute_session": False,
            "start_hypothesis_first_rth_utc": None,
            "start_hypothesis_first_rth_ny": None,
            "start_hypothesis_last_rth_utc": None,
            "start_hypothesis_last_rth_ny": None,
            "start_hypothesis_rth_rows": 0,
            "start_hypothesis_missing_minutes": None,
            "start_hypothesis_complete_expected_grid": False,
            "end_hypothesis_first_rth_utc": None,
            "end_hypothesis_first_rth_ny": None,
            "end_hypothesis_last_rth_utc": None,
            "end_hypothesis_last_rth_ny": None,
            "end_hypothesis_rth_rows": 0,
            "end_hypothesis_missing_minutes": None,
            "end_hypothesis_complete_expected_grid": False,
            "per_day_label_pattern": "UNKNOWN",
        }
        if session is None:
            base["session_status"] = "NOT_A_NYSE_SESSION"
            evidence_rows.append(base)
            for clock in AUCTION_CHECK_TIMES:
                auction_rows.append({
                    "snapshot_id": SNAPSHOT_ID, "producer_sha": PRODUCER_SHA,
                    "date_ny": day, "requested_time_ny": clock,
                    "timestamp_utc": None, "timestamp_ny": None,
                    **{col: None for col in OHLCV}, "bar_present": False,
                    "official_close": None,
                    "official_close_comparison": "NOT_AVAILABLE_NO_INDEPENDENT_CLOSE_SOURCE",
                    "auction_attribution": "UNKNOWN_NO_TRADE_CONDITION_OR_TICK_DATA",
                })
            continue

        opening = pd.Timestamp(session["market_open"]).tz_convert("UTC")
        closing = pd.Timestamp(session["market_close"]).tz_convert("UTC")
        duration = int((closing - opening) / pd.Timedelta(minutes=1))
        base["session_status"] = "SCHEDULED_SESSION"
        base["scheduled_rth_minutes"] = duration
        base["normal_390_minute_session"] = duration == 390

        if not day_bars.empty:
            first_utc, first_ny = _format_pair(day_bars.iloc[0]["timestamp_utc"])
            last_utc, last_ny = _format_pair(day_bars.iloc[-1]["timestamp_utc"])
            base["first_source_timestamp_utc"] = first_utc
            base["first_source_timestamp_ny"] = first_ny
            base["last_source_timestamp_utc"] = last_utc
            base["last_source_timestamp_ny"] = last_ny

        local_day = pd.Timestamp(day).tz_localize(NY)
        pre_start = (local_day + pd.Timedelta(hours=4)).tz_convert("UTC")
        post_end = (local_day + pd.Timedelta(hours=20)).tz_convert("UTC")
        pre = day_bars.loc[(day_bars["timestamp_utc"] >= pre_start)
                           & (day_bars["timestamp_utc"] < opening)]
        post = day_bars.loc[(day_bars["timestamp_utc"] >= closing)
                            & (day_bars["timestamp_utc"] < post_end)]
        base["premarket_timestamp_rows"] = int(len(pre))
        base["has_premarket_timestamp_0400_to_open"] = bool(len(pre))
        base["afterhours_timestamp_rows"] = int(len(post))
        base["has_afterhours_timestamp_close_to_2000"] = bool(len(post))

        start_rows = day_bars.loc[(day_bars["timestamp_utc"] >= opening)
                                  & (day_bars["timestamp_utc"] < closing)]
        end_rows = day_bars.loc[(day_bars["timestamp_utc"] > opening)
                                & (day_bars["timestamp_utc"] <= closing)]
        expected_start = pd.date_range(opening, closing, freq="min", inclusive="left")
        expected_end = pd.date_range(opening, closing, freq="min", inclusive="right")
        start_seen = pd.DatetimeIndex(start_rows["timestamp_utc"].drop_duplicates())
        end_seen = pd.DatetimeIndex(end_rows["timestamp_utc"].drop_duplicates())
        start_missing = len(expected_start.difference(start_seen))
        end_missing = len(expected_end.difference(end_seen))
        if len(start_rows):
            sfirst, sfirst_ny = _format_pair(start_rows.iloc[0]["timestamp_utc"])
            slast, slast_ny = _format_pair(start_rows.iloc[-1]["timestamp_utc"])
            base.update({
                "start_hypothesis_first_rth_utc": sfirst,
                "start_hypothesis_first_rth_ny": sfirst_ny,
                "start_hypothesis_last_rth_utc": slast,
                "start_hypothesis_last_rth_ny": slast_ny,
            })
        if len(end_rows):
            efirst, efirst_ny = _format_pair(end_rows.iloc[0]["timestamp_utc"])
            elast, elast_ny = _format_pair(end_rows.iloc[-1]["timestamp_utc"])
            base.update({
                "end_hypothesis_first_rth_utc": efirst,
                "end_hypothesis_first_rth_ny": efirst_ny,
                "end_hypothesis_last_rth_utc": elast,
                "end_hypothesis_last_rth_ny": elast_ny,
            })
        start_complete = duration == 390 and start_missing == 0 and len(start_rows) == 390
        end_complete = duration == 390 and end_missing == 0 and len(end_rows) == 390
        base.update({
            "start_hypothesis_rth_rows": int(len(start_rows)),
            "start_hypothesis_missing_minutes": int(start_missing),
            "start_hypothesis_complete_expected_grid": bool(start_complete),
            "end_hypothesis_rth_rows": int(len(end_rows)),
            "end_hypothesis_missing_minutes": int(end_missing),
            "end_hypothesis_complete_expected_grid": bool(end_complete),
        })
        if start_complete and not end_complete:
            base["per_day_label_pattern"] = "START_PATTERN_ONLY_NOT_CONFIRMATION"
        elif end_complete and not start_complete:
            base["per_day_label_pattern"] = "END_PATTERN_ONLY_NOT_CONFIRMATION"
        elif start_complete and end_complete:
            base["per_day_label_pattern"] = "AMBIGUOUS_BOTH_HYPOTHESES"
        elif not day_bars.empty:
            base["per_day_label_pattern"] = "INCOMPLETE_OR_NONSTANDARD"
        evidence_rows.append(base)

        for clock in AUCTION_CHECK_TIMES:
            match = day_bars.loc[day_bars["time_ny"].eq(clock)]
            if len(match) > 1:
                raise ValueError(f"multiple rows at {day} {clock} NY")
            if match.empty:
                auction_rows.append({
                    "snapshot_id": SNAPSHOT_ID, "producer_sha": PRODUCER_SHA,
                    "date_ny": day, "requested_time_ny": clock,
                    "timestamp_utc": None, "timestamp_ny": None,
                    **{col: None for col in OHLCV}, "bar_present": False,
                    "volume_ratio_vs_1558": None, "volume_ratio_vs_1559": None,
                    "official_close": None,
                    "official_close_comparison": "NOT_AVAILABLE_NO_INDEPENDENT_CLOSE_SOURCE",
                    "auction_attribution": "UNKNOWN_NO_TRADE_CONDITION_OR_TICK_DATA",
                })
                continue
            row = match.iloc[0]
            ts_utc, ts_ny = _format_pair(row["timestamp_utc"])
            volume_1558 = day_bars.loc[day_bars["time_ny"].eq("15:58"), "volume"]
            volume_1559 = day_bars.loc[day_bars["time_ny"].eq("15:59"), "volume"]
            v1558 = float(volume_1558.iloc[0]) if len(volume_1558) else None
            v1559 = float(volume_1559.iloc[0]) if len(volume_1559) else None
            volume = float(row["volume"])
            auction_rows.append({
                "snapshot_id": SNAPSHOT_ID, "producer_sha": PRODUCER_SHA,
                "date_ny": day, "requested_time_ny": clock,
                "timestamp_utc": ts_utc, "timestamp_ny": ts_ny,
                **{col: row[col] for col in OHLCV},
                "bar_present": True,
                "volume_ratio_vs_1558": (volume / v1558) if v1558 not in (None, 0) else None,
                "volume_ratio_vs_1559": (volume / v1559) if v1559 not in (None, 0) else None,
                "official_close": None,
                "official_close_comparison": "NOT_AVAILABLE_NO_INDEPENDENT_CLOSE_SOURCE",
                "auction_attribution": "UNKNOWN_NO_TRADE_CONDITION_OR_TICK_DATA",
            })

    evidence = pd.DataFrame(evidence_rows)
    auction = pd.DataFrame(auction_rows)
    usable_days = evidence.loc[evidence["normal_390_minute_session"]
                                & evidence["rows_on_ny_date"].gt(0)]
    start_n = int(usable_days["start_hypothesis_complete_expected_grid"].sum())
    end_n = int(usable_days["end_hypothesis_complete_expected_grid"].sum())
    n_days = len(usable_days)
    if n_days and start_n == n_days and end_n == 0:
        pattern = "STRONGLY_SUPPORTED_NOT_CONFIRMED_BAR_START"
    elif n_days and end_n == n_days and start_n == 0:
        pattern = "STRONGLY_SUPPORTED_NOT_CONFIRMED_BAR_END"
    else:
        pattern = "UNKNOWN_OR_MIXED"
    summary = {
        "snapshot_id": SNAPSHOT_ID,
        "producer_sha": PRODUCER_SHA,
        "selected_dates": list(dates),
        "normal_sessions_with_data": n_days,
        "start_hypothesis_complete_days": start_n,
        "end_hypothesis_complete_days": end_n,
        "timestamp_pattern_verdict": pattern,
        "timestamp_label_confirmed": False,
        "confirmation_limitation": (
            "Observed grid alignment is not independent timestamp-label proof; "
            "trade-time correlation or authoritative provider semantics is still needed."
        ),
        "auction_attribution": "UNKNOWN_NO_TRADE_CONDITION_OR_TICK_DATA",
        "official_close_comparison": "NOT_AVAILABLE_NO_INDEPENDENT_CLOSE_SOURCE",
        "rth_execution_boundary": (
            "FAIL_CLOSED_FOR_CLOSE_AUCTION; do not model auction fills or rely on "
            "unverified auction-containing OHLC prices."
        ),
    }
    return evidence, auction, summary


def read_bounded_parquet(path: Path, dates: Iterable[str] = SAMPLE_DATES) -> pd.DataFrame:
    """Read only timestamp/OHLCV columns and selected New York calendar dates."""
    import pyarrow as pa
    import pyarrow.dataset as ds

    dataset = ds.dataset(str(path), format="parquet")
    required = {"timestamp_utc", *OHLCV}
    missing = required.difference(dataset.schema.names)
    if missing:
        raise ValueError(f"Parquet schema missing required normalized fields: {sorted(missing)}")
    timestamp_type = dataset.schema.field("timestamp_utc").type
    if not pa.types.is_timestamp(timestamp_type):
        raise ValueError(f"timestamp_utc must be a typed Arrow timestamp, found {timestamp_type}")
    field = ds.field("timestamp_utc")
    predicate = None
    for day in dates:
        local_start = pd.Timestamp(day).tz_localize(NY)
        local_end = local_start + pd.DateOffset(days=1)
        utc_start = pa.scalar(local_start.tz_convert("UTC").to_pydatetime(), type=timestamp_type)
        utc_end = pa.scalar(local_end.tz_convert("UTC").to_pydatetime(), type=timestamp_type)
        day_filter = (field >= utc_start) & (field < utc_end)
        predicate = day_filter if predicate is None else predicate | day_filter
    table = dataset.to_table(columns=["timestamp_utc", *OHLCV], filter=predicate)
    return table.to_pandas()


def _research_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "UNKNOWN"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, required=True,
                        help="Path to the existing mounted tsm_normalized.parquet")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New empty directory for bounded CSV/JSON outputs")
    args = parser.parse_args()
    if not args.parquet.is_file():
        raise FileNotFoundError(args.parquet)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bars = read_bounded_parquet(args.parquet)
    evidence, auction, summary = analyze_bars(bars)
    run_info = {
        **summary,
        "research_commit_sha": _research_sha(),
        "drive_file_id": DRIVE_FILE_ID,
        "input_path": str(args.parquet),
        "input_size_bytes": args.parquet.stat().st_size,
        "read_columns": ["timestamp_utc", *OHLCV],
        "read_dates_ny": list(SAMPLE_DATES),
        "final_gate_rerun": False,
        "raw_mutated": False,
    }
    evidence.to_csv(args.output_dir / "bounded_timestamp_evidence.csv", index=False)
    auction.to_csv(args.output_dir / "closing_auction_comparison.csv", index=False)
    (args.output_dir / "run_info.json").write_text(
        json.dumps(run_info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "evidence_csv": str(args.output_dir / "bounded_timestamp_evidence.csv"),
        "auction_csv": str(args.output_dir / "closing_auction_comparison.csv"),
        "run_info": str(args.output_dir / "run_info.json"),
        "timestamp_pattern_verdict": summary["timestamp_pattern_verdict"],
        "auction_attribution": summary["auction_attribution"],
    }, indent=2))


if __name__ == "__main__":
    main()
