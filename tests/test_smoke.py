import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from market_research_core.errors import PolicyViolation
from market_research_core.policy import INSTRUMENTS, PINNED_SNAPSHOT
from market_research_core.smoke import FUTURES_CONTRACTS, run_drive_smoke_test


def make_smoke_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / PINNED_SNAPSHOT.run_name
    root.mkdir()
    (root / "final_gate_manifest.json").write_text(
        json.dumps({"git_sha": "06359faf7ef4", "overall_verdict": "SCOPED_GO"})
    )
    pd.DataFrame(
        [{"instrument": symbol, "verdict": policy.verdict} for symbol, policy in INSTRUMENTS.items()]
    ).to_csv(root / "final_gate_summary.csv", index=False)
    pd.DataFrame(columns=["contract", "start_utc", "end_utc"]).to_csv(
        root / "nq_unresolved_exclusions.csv", index=False
    )
    pd.DataFrame(columns=["contract", "start_utc", "end_utc"]).to_csv(
        root / "es_unresolved_exclusions.csv", index=False
    )
    pd.DataFrame(columns=["first_missing_utc", "last_missing_utc"]).to_csv(
        root / "soxx_rth_missing_intervals.csv", index=False
    )

    for symbol in ("TSM", "SOXX"):
        filename = INSTRUMENTS[symbol].normalized_pattern
        pd.DataFrame({"timestamp": ["2020-01-01T00:00:00Z"]}).to_parquet(root / filename, index=False)
        monkeypatch.setitem(INSTRUMENTS, symbol, replace(INSTRUMENTS[symbol], expected_rows=1))
    for symbol, contracts in FUTURES_CONTRACTS.items():
        folder = root / "normalized_futures" / symbol
        folder.mkdir(parents=True)
        for contract in contracts:
            pd.DataFrame({"timestamp": ["2020-01-01T00:00:00Z"]}).to_parquet(
                folder / f"{contract}.parquet", index=False
            )
        monkeypatch.setitem(
            INSTRUMENTS, symbol, replace(INSTRUMENTS[symbol], expected_rows=len(contracts))
        )
    return root


def test_drive_smoke_passes_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = make_smoke_run(tmp_path, monkeypatch)
    before = {p.relative_to(root): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    result = run_drive_smoke_test(root)
    after = {p.relative_to(root): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    assert result["status"] == "PASS"
    assert result["instruments"]["NQ"]["continuous_contract_created"] is False
    assert before == after


def test_drive_smoke_fails_on_row_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = make_smoke_run(tmp_path, monkeypatch)
    monkeypatch.setitem(INSTRUMENTS, "TSM", replace(INSTRUMENTS["TSM"], expected_rows=2))
    with pytest.raises(PolicyViolation, match="TSM row count mismatch"):
        run_drive_smoke_test(root)

