import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .errors import PolicyViolation, SnapshotMismatch

TXF_RUN_NAME = "20261014_213932_404db609cf13"
TXF_PRODUCER_SHA = "404db609cf136aae050e09f1e7712a7894f6ab6d"
# Producer-computed canonical hash recorded in run_info/validation evidence.
TXF_SAMPLE_MATRIX_SHA256 = "13b24c1defe3b58eba7092adfe4cf66a9e5f067b7862c8aff4c603d4962c4c9f"
# Raw-byte hash of diagnostics/predeclared_sample_matrix.csv in the pinned run.
TXF_SAMPLE_MATRIX_FILE_SHA256 = "a1d8ac2de0265aea2fa826ba2478d6b9fbd186fc76299365433325ce0ac0689f"
TXF_VERDICT = "PASS WITH CAVEATS"
TXF_EXPECTED_ROWS = 206_938
TXF_EXPECTED_DAYS = 185
TXF_EXPECTED_REQUESTS = 106
TXF_EXPECTED_PRIMARY_FILES = 58
TXF_VALUE_COLUMNS = ("open", "high", "low", "close", "volume")

@dataclass(frozen=True)
class TXFLoadMetadata:
    run_dir: Path
    producer_git_sha: str
    verdict: str
    scope: str
    source_files: tuple[Path, ...]
    rows_before_filter: int
    rows_after_filter: int
    requested_start: str | None
    requested_end: str | None

@dataclass(frozen=True)
class LoadedTXFBars:
    frame: pd.DataFrame
    metadata: TXFLoadMetadata

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _parse_run_info(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise SnapshotMismatch("TXF run_info.txt is missing")
    values: dict[str, object] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        try:
            values[key] = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            values[key] = raw.strip()
    return values

class TXFAdapter:
    """Read the pinned, formally validated TXF sample without re-running QA."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.resolve()
        self.scope = self._verify_run()
        self.source_files = self._resolve_primary_files()

    @classmethod
    def from_run_dir(cls, run_dir: str | Path) -> "TXFAdapter":
        return cls(Path(run_dir))

    def _verify_run(self) -> str:
        if self.run_dir.name != TXF_RUN_NAME:
            raise SnapshotMismatch(f"Unpinned TXF run directory: {self.run_dir.name}")
        info = _parse_run_info(self.run_dir / "run_info.txt")
        expected = {
            "git_commit": TXF_PRODUCER_SHA,
            "execution_status": "COMPLETED",
            "validation_phase": "final_cross_year_historical_integrity",
            "sample_matrix_sha256": TXF_SAMPLE_MATRIX_SHA256,
            "sample_trade_date_count": TXF_EXPECTED_DAYS,
            "expected_logical_request_count": TXF_EXPECTED_REQUESTS,
            "logical_request_count": TXF_EXPECTED_REQUESTS,
            "api_error_count": 0,
            "final_integrity_status": TXF_VERDICT,
        }
        for key, value in expected.items():
            if info.get(key) != value:
                raise SnapshotMismatch(f"TXF run_info mismatch for {key}: {info.get(key)!r}")
        matrix = self.run_dir / "diagnostics" / "predeclared_sample_matrix.csv"
        if not matrix.is_file() or _sha256(matrix) != TXF_SAMPLE_MATRIX_FILE_SHA256:
            raise SnapshotMismatch("TXF sample matrix checksum mismatch")
        metrics_path = self.run_dir / "diagnostics" / "final_historical_integrity_summary.csv"
        if not metrics_path.is_file():
            raise SnapshotMismatch("TXF final integrity summary is missing")
        metrics_frame = pd.read_csv(metrics_path)
        if not {"metric", "value"}.issubset(metrics_frame.columns):
            raise SnapshotMismatch("TXF final integrity summary schema mismatch")
        metrics = dict(zip(metrics_frame["metric"].astype(str), metrics_frame["value"]))
        required_metrics = {
            "trade_dates_tested": TXF_EXPECTED_DAYS, "continuous_rows": TXF_EXPECTED_ROWS,
            "schema_failures": 0, "OHLC_invalid_rows": 0, "duplicate_rows": 0,
            "outside_session_rows": 0, "missing_contract_month_rows": 0,
            "partial_session_switches": 0, "rollover_OHLC_mismatches": 0,
            "active_asymmetric_missing_rows": 0, "external_spot_check_matches": 80,
            "external_spot_check_unresolved": 0,
        }
        for key, expected_value in required_metrics.items():
            try:
                actual = int(metrics.get(key))
            except (TypeError, ValueError):
                raise SnapshotMismatch(f"TXF integrity metric is missing: {key}") from None
            if actual != expected_value:
                raise SnapshotMismatch(f"TXF integrity metric mismatch for {key}: {actual}")
        return str(info.get("scope_language", "")).strip()

    def _resolve_primary_files(self) -> tuple[Path, ...]:
        plan_path = self.run_dir / "diagnostics" / "predeclared_query_plan.csv"
        if not plan_path.is_file():
            raise SnapshotMismatch("TXF predeclared query plan is missing")
        plan = pd.read_csv(plan_path)
        required = {"name", "session", "contract_query", "role"}
        if not required.issubset(plan.columns):
            raise SnapshotMismatch("TXF query plan schema mismatch")
        primary = plan.loc[(plan["role"] == "primary") & (plan["contract_query"] == "1!") & plan["session"].isin(["regular", "afterhours"])]
        files = tuple(self.run_dir / "data" / f"{name}.parquet" for name in primary["name"])
        if len(files) != TXF_EXPECTED_PRIMARY_FILES:
            raise SnapshotMismatch(f"Expected {TXF_EXPECTED_PRIMARY_FILES} TXF primary files; found {len(files)}")
        missing = [path.name for path in files if not path.is_file()]
        if missing:
            raise SnapshotMismatch(f"TXF primary files are missing: {', '.join(missing[:3])}")
        return files

    def load(self, *, start: str | None = None, end: str | None = None) -> LoadedTXFBars:
        frames: list[pd.DataFrame] = []
        for path in self.source_files:
            frame = pd.read_parquet(path).copy()
            required = {"timestamp", "actual_contract_month", "session", *TXF_VALUE_COLUMNS}
            if not required.issubset(frame.columns):
                raise PolicyViolation(f"TXF file schema mismatch: {path.name}")
            if frame["actual_contract_month"].isna().any():
                raise PolicyViolation(f"TXF actual contract month is missing: {path.name}")
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="raise")
            if frame["timestamp"].dt.tz is None:
                raise PolicyViolation(f"TXF timestamp lacks timezone: {path.name}")
            frame["timestamp"] = frame["timestamp"].dt.tz_convert("Asia/Taipei")
            frame["_source_file"] = path.name
            frames.append(frame)
        combined = pd.concat(frames, ignore_index=True)
        before = len(combined)
        if before != TXF_EXPECTED_ROWS:
            raise SnapshotMismatch(f"TXF approved sample row count mismatch: expected {TXF_EXPECTED_ROWS}, got {before}")
        identity = ["timestamp", "actual_contract_month", "session", *TXF_VALUE_COLUMNS]
        if combined.duplicated(subset=identity).any():
            raise PolicyViolation("TXF approved sample contains duplicate bars")
        if combined.duplicated(subset=["timestamp", "session"]).any():
            raise PolicyViolation("TXF approved sample contains conflicting timestamps")
        combined = combined.set_index("timestamp", drop=False).sort_index()
        if start is not None:
            combined = combined.loc[combined.index >= pd.to_datetime(start, utc=True).tz_convert("Asia/Taipei")]
        if end is not None:
            combined = combined.loc[combined.index <= pd.to_datetime(end, utc=True).tz_convert("Asia/Taipei")]
        metadata = TXFLoadMetadata(self.run_dir, TXF_PRODUCER_SHA, TXF_VERDICT, self.scope, self.source_files, before, len(combined), start, end)
        return LoadedTXFBars(combined, metadata)
