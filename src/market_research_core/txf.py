import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .errors import PolicyViolation, SnapshotMismatch


TXF_VERDICT = "SCOPED_GO"
TXF_EXPECTED_ROWS = 206_938
TXF_EXPECTED_DAYS = 185
TXF_TIMESTAMP_COLUMNS = ("timestamp", "datetime", "date", "time")
TXF_CONTRACT_COLUMNS = ("contract_month", "contractMonth", "contract", "symbol")
TXF_VALUE_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class TXFLoadMetadata:
    manifest_path: Path
    verdict: str
    scope: str
    source_files: tuple[Path, ...]
    rows_before_filter: int
    rows_after_filter: int
    exact_duplicates_removed: int
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


def _first_column(frame: pd.DataFrame, candidates: tuple[str, ...], label: str) -> str:
    lookup = {str(column).lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    raise PolicyViolation(f"TXF data lacks {label}; expected one of {', '.join(candidates)}")


class TXFAdapter:
    """Read only an explicitly approved TXF validation slice.

    This adapter does not promote the 473 weekly cache partitions to a validated
    full-history dataset. Every readable parquet must be named and hashed in the
    supplied approval manifest.
    """

    def __init__(self, cache_dir: Path, manifest_path: Path):
        self.cache_dir = cache_dir.resolve()
        self.manifest_path = manifest_path.resolve()
        self.manifest = self._verify_manifest()

    @classmethod
    def from_manifest(
        cls, cache_dir: str | Path, manifest_path: str | Path
    ) -> "TXFAdapter":
        return cls(Path(cache_dir), Path(manifest_path))

    def _verify_manifest(self) -> dict:
        if not self.manifest_path.is_file():
            raise SnapshotMismatch("TXF approval manifest is missing")
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotMismatch("TXF approval manifest is unreadable") from exc

        if manifest.get("dataset") != "TXF_1MIN":
            raise SnapshotMismatch("TXF manifest dataset must be TXF_1MIN")
        if manifest.get("verdict") != TXF_VERDICT:
            raise SnapshotMismatch("TXF manifest verdict must be SCOPED_GO")
        if manifest.get("expected_rows") != TXF_EXPECTED_ROWS:
            raise SnapshotMismatch("TXF approved row count must be 206938")
        if manifest.get("expected_days") != TXF_EXPECTED_DAYS:
            raise SnapshotMismatch("TXF approved day count must be 185")
        if not str(manifest.get("scope", "")).strip():
            raise SnapshotMismatch("TXF manifest scope is required")
        partitions = manifest.get("partitions")
        if not isinstance(partitions, list) or not partitions:
            raise SnapshotMismatch("TXF manifest must contain approved partitions")

        names: set[str] = set()
        for item in partitions:
            if not isinstance(item, dict):
                raise SnapshotMismatch("Invalid TXF partition entry")
            name = str(item.get("file", ""))
            if Path(name).name != name or not name.endswith(".parquet"):
                raise SnapshotMismatch(f"Unsafe TXF partition name: {name}")
            if name in names:
                raise SnapshotMismatch(f"Duplicate TXF partition in manifest: {name}")
            names.add(name)
            expected_hash = str(item.get("sha256", "")).lower()
            if len(expected_hash) != 64 or any(c not in "0123456789abcdef" for c in expected_hash):
                raise SnapshotMismatch(f"Invalid SHA256 for TXF partition: {name}")
        return manifest

    def _read_partition(self, item: dict) -> pd.DataFrame:
        path = self.cache_dir / item["file"]
        if not path.is_file():
            raise SnapshotMismatch(f"Approved TXF partition is missing: {path.name}")
        actual_hash = _sha256(path)
        if actual_hash != item["sha256"].lower():
            raise SnapshotMismatch(f"TXF partition checksum mismatch: {path.name}")

        sidecar_path = self.cache_dir / f"{path.stem}.json"
        if not sidecar_path.is_file():
            raise SnapshotMismatch(f"TXF sidecar is missing: {sidecar_path.name}")
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotMismatch(f"TXF sidecar is unreadable: {sidecar_path.name}") from exc
        if sidecar.get("sha256") != actual_hash:
            raise SnapshotMismatch(f"TXF sidecar checksum mismatch: {sidecar_path.name}")
        params = sidecar.get("params", {})
        if params.get("product") != "TXF" or str(params.get("timeframe")) != "1":
            raise SnapshotMismatch(f"TXF sidecar request is not TXF 1-minute: {sidecar_path.name}")

        frame = pd.read_parquet(path).copy()
        timestamp_column = _first_column(frame, TXF_TIMESTAMP_COLUMNS, "timestamp")
        contract_column = _first_column(frame, TXF_CONTRACT_COLUMNS, "actual contract month")
        missing_values = [column for column in TXF_VALUE_COLUMNS if column not in frame.columns]
        if missing_values:
            raise PolicyViolation(f"TXF data lacks OHLCV columns: {', '.join(missing_values)}")
        frame["_timestamp_taipei"] = pd.to_datetime(frame[timestamp_column], errors="raise")
        if frame["_timestamp_taipei"].dt.tz is None:
            frame["_timestamp_taipei"] = frame["_timestamp_taipei"].dt.tz_localize(
                "Asia/Taipei", ambiguous="raise", nonexistent="raise"
            )
        else:
            frame["_timestamp_taipei"] = frame["_timestamp_taipei"].dt.tz_convert("Asia/Taipei")
        if frame[contract_column].isna().any() or (frame[contract_column].astype(str).str.strip() == "").any():
            raise PolicyViolation("TXF actual contract month contains missing values")
        frame["_actual_contract_month"] = frame[contract_column].astype(str).str.strip()
        frame["_source_partition"] = path.name
        return frame

    def load(self, *, start: str | None = None, end: str | None = None) -> LoadedTXFBars:
        frames = [self._read_partition(item) for item in self.manifest["partitions"]]
        combined = pd.concat(frames, ignore_index=True)
        before = len(combined)

        identity = ["_timestamp_taipei", "_actual_contract_month", *TXF_VALUE_COLUMNS]
        timestamp_groups = combined.groupby("_timestamp_taipei", sort=False, dropna=False)
        for timestamp, group in timestamp_groups:
            if len(group) > 1 and len(group[identity].drop_duplicates()) != 1:
                raise PolicyViolation(f"Conflicting TXF duplicate timestamp: {timestamp}")
        combined = combined.drop_duplicates(subset=identity, keep="first")
        duplicates_removed = before - len(combined)
        combined = combined.set_index("_timestamp_taipei", drop=False).sort_index()

        approved_start = pd.Timestamp(self.manifest["approved_start"], tz="Asia/Taipei")
        approved_end = pd.Timestamp(self.manifest["approved_end"], tz="Asia/Taipei")
        requested_start = pd.Timestamp(start, tz="Asia/Taipei") if start else approved_start
        requested_end = pd.Timestamp(end, tz="Asia/Taipei") if end else approved_end
        if requested_start < approved_start or requested_end > approved_end or requested_end < requested_start:
            raise PolicyViolation("Requested TXF range is outside the approved validation slice")
        filtered = combined.loc[(combined.index >= requested_start) & (combined.index <= requested_end)].copy()
        if start is None and end is None and len(filtered) != TXF_EXPECTED_ROWS:
            raise SnapshotMismatch(
                f"TXF approved slice row count mismatch: expected {TXF_EXPECTED_ROWS}, got {len(filtered)}"
            )

        metadata = TXFLoadMetadata(
            manifest_path=self.manifest_path,
            verdict=TXF_VERDICT,
            scope=self.manifest["scope"],
            source_files=tuple(self.cache_dir / item["file"] for item in self.manifest["partitions"]),
            rows_before_filter=before,
            rows_after_filter=len(filtered),
            exact_duplicates_removed=duplicates_removed,
            requested_start=start,
            requested_end=end,
        )
        return LoadedTXFBars(filtered, metadata)
