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


def test_final_gate_timestamp_utc_schema_is_supported(tmp_path: Path):
    root = make_run(tmp_path)
    pd.DataFrame({"timestamp_utc": ["2020-01-01T14:30:00Z"], "close": [10.0]}).to_parquet(
        root / "tsm_normalized.parquet", index=False
    )
    loaded = MarketDataClient.from_run_dir(root).load("TSM")
    assert str(loaded.frame.index.dtype) == "datetime64[ns, UTC]"


def test_scoped_go_exclusion_is_mandatory(tmp_path: Path):
    root = make_run(tmp_path)
    (root / "normalized_futures" / "NQ").mkdir(parents=True)
    pd.DataFrame(
        {"timestamp": ["2020-01-01T14:30:00Z", "2020-01-01T14:31:00Z"], "close": [1, 2]}
    ).to_parquet(root / "normalized_futures" / "NQ" / "NQH5.parquet", index=False)
    pd.DataFrame(
        {
            "contract": ["NQH5"],
            "start_utc": ["2020-01-01T14:30:00Z"],
            "end_utc": ["2020-01-01T14:31:00Z"],
        }
    ).to_csv(root / "nq_unresolved_exclusions.csv", index=False)
    loaded = MarketDataClient.from_run_dir(root).load("NQ", contract="NQH5")
    assert list(loaded.frame["close"]) == [2]
    assert loaded.metadata.excluded_rows == 1


def test_missing_exclusion_file_fails_closed(tmp_path: Path):
    root = make_run(tmp_path)
    (root / "normalized_futures" / "NQ").mkdir(parents=True)
    pd.DataFrame({"timestamp": ["2020-01-01T14:30:00Z"]}).to_parquet(
        root / "normalized_futures" / "NQ" / "NQH5.parquet", index=False
    )
    with pytest.raises(PolicyViolation, match="Expected exactly one"):
        MarketDataClient.from_run_dir(root).load("NQ", contract="NQH5")


def test_continuous_future_inference_is_forbidden(tmp_path: Path):
    root = make_run(tmp_path)
    with pytest.raises(PolicyViolation, match="forbidden"):
        MarketDataClient.from_run_dir(root).load("ES", continuous=True)


def test_futures_contract_is_required(tmp_path: Path):
    root = make_run(tmp_path)
    with pytest.raises(PolicyViolation, match="explicit quarterly contract"):
        MarketDataClient.from_run_dir(root).load("NQ")


def test_soxx_real_exclusion_columns_are_inclusive(tmp_path: Path):
    root = make_run(tmp_path)
    pd.DataFrame(
        {"timestamp": ["2017-01-05T14:30:00Z", "2017-01-05T15:56:00Z", "2017-01-05T15:57:00Z"]}
    ).to_parquet(root / "soxx_normalized.parquet", index=False)
    pd.DataFrame(
        {
            "first_missing_utc": ["2017-01-05T14:30:00Z"],
            "last_missing_utc": ["2017-01-05T15:56:00Z"],
        }
    ).to_csv(root / "soxx_rth_missing_intervals.csv", index=False)
    loaded = MarketDataClient.from_run_dir(root).load("SOXX")
    assert len(loaded.frame) == 1
    assert loaded.metadata.excluded_rows == 2


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



def test_tsm_unknown_execution_semantics_are_research_only(tmp_path: Path):
    root = make_run(tmp_path)
    pd.DataFrame(
        {"timestamp_utc": ["2020-01-02T14:30:00Z"], "close": [10.0]}
    ).to_parquet(root / "tsm_normalized.parquet", index=False)

    bars = MarketDataClient.from_run_dir(root).load(market="IBKR", symbol="TSM")
    row = bars.frame.iloc[0]

    assert row["timestamp_label"] == "UNKNOWN"
    assert row["session"] == "UNKNOWN"
    assert pd.isna(row["bar_start"])
    assert pd.isna(row["bar_end"])
    assert pd.isna(row["available_at"])
    assert not row["buy_execution_eligible"]
    assert not row["sell_execution_eligible"]
    assert row["execution_rejection_reason"] == "TIMESTAMP_LABEL_OR_SESSION_UNKNOWN"
    assert bars.provenance.permitted_usage_scope == "RESEARCH_ONLY"
