from pathlib import Path

import pandas as pd

from .errors import PolicyViolation
from .models import InstrumentPolicy


TIMESTAMP_CANDIDATES = ("timestamp_utc", "timestamp", "datetime", "date", "time")
EXCLUSION_START_CANDIDATES = (
    "start", "start_ts", "start_time", "start_utc", "window_start",
    "missing_start", "first_missing_utc",
)
EXCLUSION_END_CANDIDATES = (
    "end", "end_ts", "end_time", "end_utc", "window_end",
    "missing_end", "last_missing_utc",
)


def _first_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    lookup = {str(c).lower(): str(c) for c in frame.columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    raise PolicyViolation(f"Expected one of columns: {', '.join(candidates)}")


def normalized_path(root: Path, policy: InstrumentPolicy, contract: str | None) -> Path:
    if "{contract}" in policy.normalized_pattern:
        if not contract:
            raise PolicyViolation(f"{policy.symbol} requires an explicit quarterly contract")
        normalized_contract = contract.upper().strip()
        if not normalized_contract.startswith(policy.symbol):
            raise PolicyViolation(f"Contract {contract} does not belong to {policy.symbol}")
        pattern = policy.normalized_pattern.format(contract=normalized_contract)
    else:
        if contract:
            raise PolicyViolation(f"{policy.symbol} does not accept a futures contract")
        pattern = policy.normalized_pattern
    direct = root / pattern
    matches = [direct] if direct.is_file() else list(root.rglob(Path(pattern).name))
    if len(matches) != 1:
        raise PolicyViolation(
            f"Expected exactly one {pattern}; found {len(matches)}"
        )
    return matches[0]


def read_bars(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    ts_col = _first_column(frame, TIMESTAMP_CANDIDATES)
    frame = frame.copy()
    parsed = pd.to_datetime(frame[ts_col], utc=True, errors="raise")
    # pandas 3 may preserve Arrow microsecond resolution; normalize the UTC contract to nanoseconds.
    frame[ts_col] = parsed.dt.as_unit("ns")
    frame = frame.set_index(ts_col, drop=False).sort_index()
    if frame.index.has_duplicates:
        raise PolicyViolation("Normalized data contains duplicate timestamps")
    return frame


def apply_exclusions(
    frame: pd.DataFrame,
    root: Path,
    policy: InstrumentPolicy,
    contract: str | None,
) -> tuple[pd.DataFrame, int]:
    if not policy.exclusions_filename:
        return frame, 0
    matches = list(root.rglob(policy.exclusions_filename))
    if len(matches) != 1:
        raise PolicyViolation(
            f"Expected exactly one {policy.exclusions_filename}; found {len(matches)}"
        )
    exclusions = pd.read_csv(matches[0])
    if contract and "contract" in exclusions.columns:
        exclusions = exclusions.loc[
            exclusions["contract"].astype(str).str.upper() == contract.upper()
        ]
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
        if policy.exclusion_end_inclusive:
            in_window = (frame.index >= start_ts) & (frame.index <= end_ts)
        else:
            in_window = (frame.index >= start_ts) & (frame.index < end_ts)
        keep &= ~in_window
    filtered = frame.loc[keep.to_numpy()].copy()
    return filtered, len(frame) - len(filtered)

