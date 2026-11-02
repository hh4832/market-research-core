from pathlib import Path

import pandas as pd

from .errors import PolicyViolation
from .models import InstrumentPolicy


TIMESTAMP_CANDIDATES = ("timestamp", "datetime", "date", "time")
EXCLUSION_START_CANDIDATES = ("start", "start_ts", "start_time", "window_start", "missing_start")
EXCLUSION_END_CANDIDATES = ("end", "end_ts", "end_time", "window_end", "missing_end")


def _first_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    lookup = {str(c).lower(): str(c) for c in frame.columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    raise PolicyViolation(f"Expected one of columns: {', '.join(candidates)}")


def normalized_path(root: Path, policy: InstrumentPolicy) -> Path:
    direct = root / policy.normalized_filename
    matches = [direct] if direct.is_file() else list(root.rglob(policy.normalized_filename))
    if len(matches) != 1:
        raise PolicyViolation(
            f"Expected exactly one {policy.normalized_filename}; found {len(matches)}"
        )
    return matches[0]


def read_bars(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    ts_col = _first_column(frame, TIMESTAMP_CANDIDATES)
    frame = frame.copy()
    frame[ts_col] = pd.to_datetime(frame[ts_col], utc=True, errors="raise")
    frame = frame.set_index(ts_col, drop=False).sort_index()
    if frame.index.has_duplicates:
        raise PolicyViolation("Normalized data contains duplicate timestamps")
    return frame


def apply_exclusions(frame: pd.DataFrame, root: Path, policy: InstrumentPolicy) -> tuple[pd.DataFrame, int]:
    if not policy.exclusions_filename:
        return frame, 0
    matches = list(root.rglob(policy.exclusions_filename))
    if len(matches) != 1:
        raise PolicyViolation(
            f"Expected exactly one {policy.exclusions_filename}; found {len(matches)}"
        )
    exclusions = pd.read_csv(matches[0])
    if exclusions.empty:
        return frame, 0
    start_col = _first_column(exclusions, EXCLUSION_START_CANDIDATES)
    end_col = _first_column(exclusions, EXCLUSION_END_CANDIDATES)
    keep = pd.Series(True, index=frame.index)
    for start, end in zip(exclusions[start_col], exclusions[end_col]):
        start_ts = pd.to_datetime(start, utc=True, errors="raise")
        end_ts = pd.to_datetime(end, utc=True, errors="raise")
        if end_ts < start_ts:
            raise PolicyViolation("Exclusion interval ends before it starts")
        keep &= ~((frame.index >= start_ts) & (frame.index <= end_ts))
    filtered = frame.loc[keep.to_numpy()].copy()
    return filtered, len(frame) - len(filtered)

