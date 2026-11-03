from __future__ import annotations

import pandas as pd

from market_research_core.ibkr_tsm_audit import SAMPLE_DATES, analyze_bars


def bars_for_day(day: str, *, include_1600: bool = True) -> pd.DataFrame:
    open_ny = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    rth = pd.date_range(open_ny, periods=390, freq="min")
    times = list(rth)
    # Synthetic only: the label of 16:00 is intentionally unresolved.
    if include_1600:
        times.extend(pd.date_range(f"{day} 16:00", periods=3, freq="min", tz="America/New_York"))
    times.insert(0, pd.Timestamp(f"{day} 04:00", tz="America/New_York"))
    return pd.DataFrame({
        "timestamp_utc": pd.DatetimeIndex(times).tz_convert("UTC"),
        "open": [100.0] * len(times),
        "high": [101.0] * len(times),
        "low": [99.0] * len(times),
        "close": [100.0] * len(times),
        "volume": [1000] * len(times),
    })


def test_sample_dates_are_fixed_and_predeclared():
    assert SAMPLE_DATES == (
        "2016-10-20", "2016-10-21", "2016-10-25", "2016-10-28",
        "2016-11-04", "2016-11-07", "2016-11-08",
    )


def test_1600_observation_can_make_start_end_hypotheses_ambiguous():
    frame = bars_for_day("2016-10-20", include_1600=True)
    evidence, auction, summary = analyze_bars(frame, dates=("2016-10-20",))

    row = evidence.iloc[0]
    assert row.start_hypothesis_first_rth_ny.startswith("2016-10-20T09:30")
    assert row.start_hypothesis_last_rth_ny.startswith("2016-10-20T15:59")
    assert row.start_hypothesis_rth_rows == 390
    assert row.end_hypothesis_first_rth_ny.startswith("2016-10-20T09:31")
    assert row.end_hypothesis_last_rth_ny.startswith("2016-10-20T16:00")
    assert row.end_hypothesis_rth_rows == 390
    assert row.per_day_label_pattern == "AMBIGUOUS_BOTH_HYPOTHESES"
    assert summary["timestamp_pattern_verdict"] == "UNKNOWN_OR_MIXED"
    assert summary["timestamp_label_confirmed"] is False
    assert bool(row.has_premarket_timestamp_0400_to_open)
    assert bool(row.has_afterhours_timestamp_close_to_2000)

    bar_1600 = auction.loc[auction.requested_time_ny.eq("16:00")].iloc[0]
    assert bool(bar_1600.bar_present)
    assert bar_1600.auction_attribution == "UNKNOWN_NO_TRADE_CONDITION_OR_TICK_DATA"
    assert bar_1600.official_close_comparison == "NOT_AVAILABLE_NO_INDEPENDENT_CLOSE_SOURCE"


def test_start_pattern_without_1600_is_still_not_confirmation():
    frame = bars_for_day("2016-10-20", include_1600=False)
    evidence, _, summary = analyze_bars(frame, dates=("2016-10-20",))
    row = evidence.iloc[0]

    assert row.start_hypothesis_complete_expected_grid
    assert not row.end_hypothesis_complete_expected_grid
    assert summary["timestamp_pattern_verdict"] == "STRONGLY_SUPPORTED_NOT_CONFIRMED_BAR_START"
    assert summary["timestamp_label_confirmed"] is False


def test_missing_requested_date_is_recorded_without_replacement():
    frame = bars_for_day("2016-10-20", include_1600=False)
    evidence, auction, summary = analyze_bars(
        frame, dates=("2016-10-20", "2016-10-21")
    )

    missing = evidence.loc[evidence.date_ny.eq("2016-10-21")].iloc[0]
    assert missing.session_status == "SCHEDULED_SESSION"
    assert missing.rows_on_ny_date == 0
    assert missing.per_day_label_pattern == "UNKNOWN"
    assert len(auction.loc[auction.date_ny.eq("2016-10-21")]) == 5
    assert summary["normal_sessions_with_data"] == 1
