import json
from pathlib import Path

import pandas as pd
import pytest

from market_research_core import MarketDataClient, PolicyViolation, SnapshotMismatch
from market_research_core.policy import INSTRUMENTS, PINNED_SNAPSHOT


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / PINNED_SNAPSHOT.run_name
    root.mkdir()
    (root / "final_gate_manifest.json").write_text(
        json.dumps({"git_sha": PINNED_SNAPSHOT.producer_git_sha, "overall_verdict": "SCOPED_GO"})
    )
    pd.DataFrame(
        [{"instrument": symbol, "verdict": policy.verdict} for symbol, policy in INSTRUMENTS.items()]
    ).to_csv(root / "final_gate_summary.csv", index=False)
    return root


def test_tsm_loads_without_synthesis(tmp_path: Path):
    root = make_run(tmp_path)
    pd.DataFrame({"timestamp": ["2020-01-01T14:30:00Z"], "close": [10.0]}).to_parquet(
        root / "tsm_normalized.parquet", index=False
    )
    loaded = MarketDataClient.from_run_dir(root).load("TSM")
    assert len(loaded.frame) == 1
    assert loaded.metadata.verdict == "GO"
    assert loaded.metadata.excluded_rows == 0


def test_scoped_go_exclusion_is_mandatory(tmp_path: Path):
    root = make_run(tmp_path)
    pd.DataFrame(
        {"timestamp": ["2020-01-01T14:30:00Z", "2020-01-01T14:31:00Z"], "close": [1, 2]}
    ).to_parquet(root / "nq_normalized.parquet", index=False)
    pd.DataFrame(
        {"start": ["2020-01-01T14:30:00Z"], "end": ["2020-01-01T14:30:00Z"]}
    ).to_csv(root / "nq_unresolved_exclusions.csv", index=False)
    loaded = MarketDataClient.from_run_dir(root).load("NQ")
    assert list(loaded.frame["close"]) == [2]
    assert loaded.metadata.excluded_rows == 1


def test_missing_exclusion_file_fails_closed(tmp_path: Path):
    root = make_run(tmp_path)
    pd.DataFrame({"timestamp": ["2020-01-01T14:30:00Z"]}).to_parquet(
        root / "nq_normalized.parquet", index=False
    )
    with pytest.raises(PolicyViolation, match="Expected exactly one"):
        MarketDataClient.from_run_dir(root).load("NQ")


def test_continuous_future_inference_is_forbidden(tmp_path: Path):
    root = make_run(tmp_path)
    with pytest.raises(PolicyViolation, match="forbidden"):
        MarketDataClient.from_run_dir(root).load("ES", continuous=True)


def test_wrong_snapshot_name_is_rejected(tmp_path: Path):
    wrong = tmp_path / "pilot"
    wrong.mkdir()
    with pytest.raises(SnapshotMismatch, match="Unpinned"):
        MarketDataClient.from_run_dir(wrong)


def test_duplicate_timestamps_fail_closed(tmp_path: Path):
    root = make_run(tmp_path)
    pd.DataFrame(
        {"timestamp": ["2020-01-01T14:30:00Z", "2020-01-01T14:30:00Z"], "close": [1, 2]}
    ).to_parquet(root / "tsm_normalized.parquet", index=False)
    with pytest.raises(PolicyViolation, match="duplicate"):
        MarketDataClient.from_run_dir(root).load("TSM")

