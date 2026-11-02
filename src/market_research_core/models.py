from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

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
    normalized_pattern: str
    exclusions_filename: str | None = None
    quarterly_contracts_only: bool = False
    exclusion_end_inclusive: bool = False


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


UsageScope = Literal["RESEARCH_ONLY", "BACKTEST", "ROBUSTNESS_ONLY"]


@dataclass(frozen=True)
class UnifiedProvenance:
    market: str
    symbol: str
    snapshot_id: str
    producer_commit_sha: str
    data_verdict: str
    source_files: tuple[str, ...]
    partition_ids: tuple[str, ...] = ()
    exclusion_rules: tuple[str, ...] = ()
    quality_mask_version: str | None = None
    permitted_usage_scope: UsageScope = "RESEARCH_ONLY"
    unavailable_intervals: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnifiedBars:
    frame: pd.DataFrame
    provenance: UnifiedProvenance
    native_metadata: Any = None


@dataclass(frozen=True)
class MarketDataConfig:
    ibkr_run_dir: Path | None = None
    txf_run_dir: Path | None = None
    taiwan_lake_root: Path | None = None


@dataclass(frozen=True)
class BacktestAssumptions:
    initial_cash: float = 1_000_000.0
    fast_window: int = 5
    slow_window: int = 20
    fee_rate: float = 0.001425
    sell_tax_rate: float = 0.0
    slippage_bps: float = 1.0
    unit_size: float = 1.0
    contract_multiplier: float | None = None
    performance_claim: bool = False


@dataclass(frozen=True)
class BacktestResult:
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    total_return: float
    max_drawdown: float
    trade_count: int
    rejections: pd.DataFrame
    provenance: dict[str, Any] = field(default_factory=dict)
