from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class SnapshotIdentity:
    run_name: str
    producer_git_sha: str
    overall_verdict: str


@dataclass(frozen=True)
class InstrumentPolicy:
    symbol: str
    verdict: str
    expected_rows: int
    normalized_filename: str
    exclusions_filename: str | None = None
    quarterly_contracts_only: bool = False


@dataclass(frozen=True)
class LoadMetadata:
    snapshot: SnapshotIdentity
    instrument: str
    verdict: str
    source_path: Path
    rows_before_filter: int
    rows_after_filter: int
    excluded_rows: int
    requested_start: str | None
    requested_end: str | None


@dataclass(frozen=True)
class LoadedBars:
    frame: pd.DataFrame
    metadata: LoadMetadata

