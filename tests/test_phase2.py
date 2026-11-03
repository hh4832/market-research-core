import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from market_research_core import (BacktestAssumptions, BacktestEngine, BacktestRejected,
    MarketDataClient, MarketDataConfig, PolicyViolation, UnifiedBars)
from market_research_core.models import UnifiedProvenance
from market_research_core.policy import INSTRUMENTS, PINNED_SNAPSHOT
from market_research_core import taiwan as tw


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_dir(path: Path, files: dict[str, pd.DataFrame | str]):
    path.mkdir(parents=True)
    hashes = {}
    for name, value in files.items():
        fp = path / name
        if isinstance(value, pd.DataFrame):
            value.to_parquet(fp, index=False)
        else:
            fp.write_text(value)
        hashes[name] = sha(fp)
    (path / "manifest.json").write_text(json.dumps({"sha256": hashes}))


def make_tw_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "data"
    fubon = root / "fubon_stock_5m"
    data_file = fubon / "data/symbol=2330/year=2025/bars.parquet"
    data_file.parent.mkdir(parents=True)
    times = pd.date_range("2025-01-02 09:00", periods=30, freq="5min", tz="Asia/Taipei")
    pd.DataFrame({"symbol": "2330", "bar_start": times, "open": range(100, 130),
        "high": range(101, 131), "low": range(99, 129), "close": range(100, 130),
        "volume_raw": 1000, "volume_unit": "lot"}).to_parquet(data_file, index=False)
    vdir = fubon / "_manifest" / tw.TW_FUBON_VERSION
    vdir.mkdir(parents=True)
    parts = pd.DataFrame([{"partition": "symbol=2330/year=2025", "relpath": data_file.relative_to(fubon).as_posix(),
        "file_sha256": sha(data_file)}])
    parts.to_parquet(vdir / "partitions.parquet", index=False)
    (vdir / "meta.json").write_text(json.dumps({"tables": {"partitions": {"sha256": sha(vdir / "partitions.parquet")}}}))
    (fubon / "_manifest/published.json").write_text(json.dumps({"seq": 68, "version_dir": tw.TW_FUBON_VERSION}))

    dates = pd.to_datetime(["2025-01-02"])
    universe_dates = pd.to_datetime(["2025-01-01", "2025-01-02"])
    quality = pd.DataFrame({"symbol": ["2330"], "date": dates, "can_use_for_price_backtest": [True],
        "research_tier": ["LEVEL_B_PRICE_GATE"], "evidence_version": [tw.TW_EVIDENCE_VERSION]})
    qdir = root / tw.TW_PINNED_PATHS["quality"]
    qdir.mkdir(parents=True)
    quality.to_parquet(qdir / "quality_mask_symbol_day.parquet", index=False)
    monkeypatch.setattr(tw, "TW_QUALITY_SHA256", sha(qdir / "quality_mask_symbol_day.parquet"))

    ca = root / tw.TW_PINNED_PATHS["corporate_actions"]
    manifest_dir(ca, {"corporate_actions_v2.parquet": pd.DataFrame({"symbol": ["2330"], "accounting_status": ["SUPPORTED"]})})
    rules = root / tw.TW_PINNED_PATHS["trading_rules"]
    manifest_dir(rules, {"trading_rules.parquet": pd.DataFrame({"symbol": ["2330"], "date": dates,
        "price_limit_status": ["VERIFIED_OFFICIAL_NEXT_DAY"], "buy_eligibility": ["ELIGIBLE_SUBJECT_TO_LIQUIDITY"],
        "sell_eligibility": ["ELIGIBLE_SUBJECT_TO_LIQUIDITY"]})})
    universe = root / tw.TW_PINNED_PATHS["universe"]
    manifest_dir(universe, {"universe_panel.parquet": pd.DataFrame({"symbol": ["2330", "2330"], "date": universe_dates,
        "official_listed": [True, True], "security_type": ["COMMON_STOCK", "COMMON_STOCK"]})})
    final_dir = root / "_tw-market-data-lake/audits/final_gate" / tw.TW_FINAL_GATE_RUN
    final_dir.mkdir(parents=True)
    (final_dir / "run_manifest.json").write_text(json.dumps({"run_id": tw.TW_FINAL_GATE_RUN, "verdict": "SCOPED GO", "pins": {
        "fubon_5m": {"seq": 68, "version_dir": tw.TW_FUBON_VERSION},
        "quality_mask": {"sha256": tw.TW_QUALITY_SHA256, "evidence_version": [tw.TW_EVIDENCE_VERSION]},
        "corporate_actions_v2": {"dir": f"/content/drive/MyDrive/data/{tw.TW_CA_DIR}"},
        "trading_rules": {"dir": f"/content/drive/MyDrive/data/{tw.TW_RULES_DIR}"},
        "universe_pit": {"dir": f"/content/drive/MyDrive/data/{tw.TW_UNIVERSE_DIR}"}}}))
    return root


def make_ibkr(tmp_path: Path) -> Path:
    root = tmp_path / PINNED_SNAPSHOT.run_name
    root.mkdir()
    (root / "final_gate_manifest.json").write_text(json.dumps({"git_sha": PINNED_SNAPSHOT.producer_git_sha, "overall_verdict": "SCOPED_GO"}))
    pd.DataFrame([{"instrument": s, "verdict": p.verdict} for s, p in INSTRUMENTS.items()]).to_csv(root / "final_gate_summary.csv", index=False)
    pd.DataFrame({"timestamp": ["2025-01-02T14:30:00Z"], "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]}).to_parquet(root / "tsm_normalized.parquet", index=False)
    return root


def test_old_ibkr_interface_and_unified_interface(tmp_path):
    root = make_ibkr(tmp_path)
    client = MarketDataClient.from_run_dir(root)
    assert len(client.load("TSM").frame) == 1
    unified = client.load(market="IBKR", symbol="TSM")
    assert unified.provenance.market == "IBKR"
    assert {"data_exists", "research_eligible", "backtest_eligible", "execution_eligible"}.issubset(unified.frame.columns)
    assert (unified.frame.available_at > unified.frame.timestamp).all()


def test_taiwan_adapter_enforces_pins_and_pit(monkeypatch, tmp_path):
    root = make_tw_root(tmp_path, monkeypatch)
    client = MarketDataClient(MarketDataConfig(taiwan_lake_root=root))
    loaded = client.load(market="TW", symbol="2330")
    assert loaded.provenance.snapshot_id == tw.TW_FINAL_GATE_RUN
    assert loaded.frame.backtest_eligible.all()
    assert loaded.frame.execution_eligible.all()
    assert (loaded.frame.available_at > loaded.frame.timestamp).all()


def test_taiwan_unknown_limit_fails_closed(monkeypatch, tmp_path):
    root = make_tw_root(tmp_path, monkeypatch)
    rdir = root / tw.TW_PINNED_PATHS["trading_rules"]
    rules = pd.read_parquet(rdir / "trading_rules.parquet")
    rules["price_limit_status"] = "UNKNOWN_NO_REFERENCE"
    rules.to_parquet(rdir / "trading_rules.parquet", index=False)
    (rdir / "manifest.json").write_text(json.dumps({"sha256": {"trading_rules.parquet": sha(rdir / "trading_rules.parquet")}}))
    loaded = MarketDataClient(MarketDataConfig(taiwan_lake_root=root)).load(market="TW", symbol="2330")
    assert not loaded.frame.execution_eligible.any()


def test_taiwan_wrong_quality_sha_is_rejected(monkeypatch, tmp_path):
    root = make_tw_root(tmp_path, monkeypatch)
    monkeypatch.setattr(tw, "TW_QUALITY_SHA256", "0" * 64)
    with pytest.raises(Exception, match="quality mask SHA|pin mismatch"):
        MarketDataClient(MarketDataConfig(taiwan_lake_root=root)).load(market="TW", symbol="2330")


def sample_unified() -> UnifiedBars:
    ts = pd.date_range("2025-01-01", periods=30, freq="min", tz="UTC")
    close = [10] * 20 + list(range(11, 21))
    frame = pd.DataFrame({"timestamp": ts, "open": close, "high": close, "low": close, "close": close,
        "volume": 100, "available_at": ts + pd.Timedelta(minutes=1), "data_exists": True,
        "research_eligible": True, "backtest_eligible": True, "execution_eligible": True})
    frame = frame.set_index("timestamp", drop=False)
    return UnifiedBars(frame, UnifiedProvenance("IBKR", "TSM", "run", "sha", "GO", ("x",), permitted_usage_scope="BACKTEST"))


def test_backtest_is_deterministic_and_executes_after_signal():
    engine = BacktestEngine()
    a = engine.run_sma(sample_unified(), BacktestAssumptions(fast_window=3, slow_window=5))
    b = engine.run_sma(sample_unified(), BacktestAssumptions(fast_window=3, slow_window=5))
    pd.testing.assert_frame_equal(a.trades, b.trades)
    assert (pd.to_datetime(a.trades.timestamp, utc=True) > pd.to_datetime(a.trades.signal_timestamp, utc=True)).all()
    assert a.provenance["performance_status"] == "RESEARCH_INTEGRATION_DEMO"


def test_futures_formal_performance_claim_is_rejected():
    with pytest.raises(BacktestRejected, match="multiplier"):
        BacktestEngine().run_sma(sample_unified(), BacktestAssumptions(fast_window=3, slow_window=5, performance_claim=True))
