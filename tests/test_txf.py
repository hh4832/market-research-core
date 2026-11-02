import hashlib
from pathlib import Path

import pandas as pd
import pytest

from market_research_core import PolicyViolation, SnapshotMismatch, TXFAdapter
from market_research_core.txf import TXF_EXPECTED_PRIMARY_FILES, TXF_PRODUCER_SHA, TXF_RUN_NAME

def make_run(tmp_path: Path) -> Path:
    root = tmp_path / TXF_RUN_NAME
    (root / "data").mkdir(parents=True)
    (root / "diagnostics").mkdir()
    matrix = root / "diagnostics" / "predeclared_sample_matrix.csv"
    matrix.write_bytes(b"sample matrix")
    digest = hashlib.sha256(matrix.read_bytes()).hexdigest()
    info = {
        "git_commit": TXF_PRODUCER_SHA, "execution_status": "COMPLETED",
        "validation_phase": "final_cross_year_historical_integrity",
        "sample_matrix_sha256": digest, "sample_trade_date_count": 185,
        "expected_logical_request_count": 106, "logical_request_count": 106,
        "api_error_count": 0, "final_integrity_status": "PASS WITH CAVEATS",
        "scope_language": "Validated deterministic historical samples.",
    }
    (root / "run_info.txt").write_text("\n".join(f"{k}={v!r}" for k, v in info.items()))
    metrics = {
        "trade_dates_tested": 185, "continuous_rows": TXF_EXPECTED_PRIMARY_FILES,
        "schema_failures": 0, "OHLC_invalid_rows": 0, "duplicate_rows": 0,
        "outside_session_rows": 0, "missing_contract_month_rows": 0,
        "partial_session_switches": 0, "rollover_OHLC_mismatches": 0,
        "active_asymmetric_missing_rows": 0, "external_spot_check_matches": 80,
        "external_spot_check_unresolved": 0,
    }
    pd.DataFrame(metrics.items(), columns=["metric", "value"]).to_csv(root / "diagnostics" / "final_historical_integrity_summary.csv", index=False)
    names = [f"sample_{i:02d}_{'regular' if i % 2 == 0 else 'afterhours'}_1bang" for i in range(TXF_EXPECTED_PRIMARY_FILES)]
    pd.DataFrame({"name": names, "session": ["regular" if i % 2 == 0 else "afterhours" for i in range(TXF_EXPECTED_PRIMARY_FILES)], "contract_query": ["1!"] * TXF_EXPECTED_PRIMARY_FILES, "role": ["primary"] * TXF_EXPECTED_PRIMARY_FILES}).to_csv(root / "diagnostics" / "predeclared_query_plan.csv", index=False)
    for i, name in enumerate(names):
        pd.DataFrame({"timestamp": [pd.Timestamp("2020-01-01", tz="Asia/Taipei") + pd.Timedelta(days=i)], "actual_contract_month": ["202001"], "session": ["regular" if i % 2 == 0 else "afterhours"], "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]}).to_parquet(root / "data" / f"{name}.parquet", index=False)
    return root

def patch_policy(monkeypatch, root: Path):
    digest = hashlib.sha256((root / "diagnostics" / "predeclared_sample_matrix.csv").read_bytes()).hexdigest()
    monkeypatch.setattr("market_research_core.txf.TXF_SAMPLE_MATRIX_SHA256", digest)
    monkeypatch.setattr("market_research_core.txf.TXF_EXPECTED_ROWS", TXF_EXPECTED_PRIMARY_FILES)

def test_pinned_formal_run_loads(monkeypatch, tmp_path: Path):
    root = make_run(tmp_path); patch_policy(monkeypatch, root)
    loaded = TXFAdapter.from_run_dir(root).load()
    assert len(loaded.frame) == TXF_EXPECTED_PRIMARY_FILES
    assert loaded.metadata.verdict == "PASS WITH CAVEATS"

def test_wrong_run_is_rejected(tmp_path: Path):
    wrong = tmp_path / "old_run"; wrong.mkdir()
    with pytest.raises(SnapshotMismatch, match="Unpinned"): TXFAdapter.from_run_dir(wrong)

def test_sample_matrix_checksum_fails_closed(tmp_path: Path):
    root = make_run(tmp_path)
    with pytest.raises(SnapshotMismatch, match="run_info mismatch|checksum"): TXFAdapter.from_run_dir(root)

def test_missing_actual_contract_fails_closed(monkeypatch, tmp_path: Path):
    root = make_run(tmp_path); patch_policy(monkeypatch, root)
    first = next((root / "data").glob("*.parquet"))
    pd.read_parquet(first).drop(columns=["actual_contract_month"]).to_parquet(first, index=False)
    with pytest.raises(PolicyViolation, match="schema"): TXFAdapter.from_run_dir(root).load()
