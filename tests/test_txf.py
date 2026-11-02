import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from market_research_core import PolicyViolation, SnapshotMismatch, TXFAdapter


def make_partition(root: Path, *, rows: list[dict] | None = None) -> tuple[str, str]:
    rows = rows or [
        {
            "timestamp": "2026-09-08 08:45:00",
            "contractMonth": "202609",
            "open": 20000,
            "high": 20001,
            "low": 19999,
            "close": 20000,
            "volume": 2,
        }
    ]
    name = "approved.parquet"
    path = root / name
    pd.DataFrame(rows).to_parquet(path, index=False)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "approved.json").write_text(
        json.dumps(
            {
                "sha256": sha,
                "params": {
                    "start": "2026-09-08",
                    "end": "2026-09-14",
                    "product": "TXF",
                    "contractMonth": "1!",
                    "timeframe": "1",
                },
            }
        ),
        encoding="utf-8",
    )
    return name, sha


def make_manifest(root: Path, name: str, sha: str, **overrides) -> Path:
    manifest = {
        "dataset": "TXF_1MIN",
        "verdict": "SCOPED_GO",
        "scope": "recorded 185-day validation slice",
        "expected_rows": 206_938,
        "expected_days": 185,
        "approved_start": "2026-09-08 00:00:00",
        "approved_end": "2026-09-14 23:59:59",
        "partitions": [{"file": name, "sha256": sha}],
    }
    manifest.update(overrides)
    path = root / "txf_access_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_bounded_load_verifies_sidecar_and_preserves_contract(tmp_path: Path):
    name, sha = make_partition(tmp_path)
    manifest = make_manifest(tmp_path, name, sha)
    loaded = TXFAdapter.from_manifest(tmp_path, manifest).load(
        start="2026-09-08 08:45:00", end="2026-09-08 08:45:00"
    )
    assert len(loaded.frame) == 1
    assert loaded.frame.iloc[0]["_actual_contract_month"] == "202609"
    assert str(loaded.frame.index.tz) == "Asia/Taipei"


def test_unapproved_full_cache_is_not_inferred(tmp_path: Path):
    name, sha = make_partition(tmp_path)
    manifest = make_manifest(tmp_path, name, sha, partitions=[])
    with pytest.raises(SnapshotMismatch, match="approved partitions"):
        TXFAdapter.from_manifest(tmp_path, manifest)


def test_checksum_mismatch_fails_closed(tmp_path: Path):
    name, sha = make_partition(tmp_path)
    manifest = make_manifest(tmp_path, name, "0" * 64)
    with pytest.raises(SnapshotMismatch, match="checksum mismatch"):
        TXFAdapter.from_manifest(tmp_path, manifest).load(start="2026-09-08", end="2026-09-09")


def test_conflicting_duplicate_fails_closed(tmp_path: Path):
    rows = [
        {"timestamp": "2026-09-08 08:45:00", "contractMonth": "202609", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"timestamp": "2026-09-08 08:45:00", "contractMonth": "202609", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1},
    ]
    name, sha = make_partition(tmp_path, rows=rows)
    manifest = make_manifest(tmp_path, name, sha)
    with pytest.raises(PolicyViolation, match="Conflicting TXF duplicate"):
        TXFAdapter.from_manifest(tmp_path, manifest).load(start="2026-09-08", end="2026-09-09")


def test_range_outside_approved_slice_fails(tmp_path: Path):
    name, sha = make_partition(tmp_path)
    manifest = make_manifest(tmp_path, name, sha)
    with pytest.raises(PolicyViolation, match="outside"):
        TXFAdapter.from_manifest(tmp_path, manifest).load(start="2026-09-01", end="2026-09-09")


def test_full_load_requires_published_row_count(tmp_path: Path):
    name, sha = make_partition(tmp_path)
    manifest = make_manifest(tmp_path, name, sha)
    with pytest.raises(SnapshotMismatch, match="row count mismatch"):
        TXFAdapter.from_manifest(tmp_path, manifest).load()
