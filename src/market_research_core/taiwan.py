from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .errors import PolicyViolation, SnapshotMismatch
from .models import UnifiedBars, UnifiedProvenance

TW_FINAL_GATE_RUN = "final_gate_20261031_154458_20eba73"
TW_FINAL_GATE_COMMIT = "20eba73"
TW_VERDICT = "SCOPED_GO"
TW_FUBON_SEQ = 68
TW_FUBON_VERSION = "versions/000068_20261025_211531_4ceb32f"
TW_QUALITY_DIR = "quality_mask_20261030_014633_fbec159"
TW_QUALITY_SHA256 = "9ec79fe02412c25dae56572a334c167c168e374a438dd71e9f4dc0e138ac6863"
TW_QUALITY_VERSION = "quality 1.0.0"
TW_EVIDENCE_VERSION = "quality_v1.0.0+eligibility_63ffd6f+recon_c111ab2+price_price_check_20261029_235425_4308c60"
TW_CA_DIR = "corporate_actions_v2_20261031_200705_75aed1d"
TW_RULES_DIR = "trading_rules_20261031_222459_c5c5a4b"
TW_UNIVERSE_DIR = "universe_pit_20261031_222454_c5c5a4b"
TW_PINNED_PATHS = {
    "quality": Path("_tw-market-data-lake/audits/quality_acceptance") / TW_QUALITY_DIR,
    "corporate_actions": Path("_tw-market-data-lake/derived/corporate_actions_v2") / TW_CA_DIR,
    "trading_rules": Path("_tw-market-data-lake/derived/trading_rules") / TW_RULES_DIR,
    "universe": Path("_tw-market-data-lake/audits/universe_pit") / TW_UNIVERSE_DIR,
}
FUND_LIKE = {"ETF", "ETN", "REIT", "FUND"}
USABLE_LIMIT_STATUS = {"VERIFIED_OFFICIAL_EVENT", "VERIFIED_OFFICIAL_NEXT_DAY", "NO_LIMIT_VERIFIED",
                       "NO_LIMIT_OFFICIAL_CLASS", "NO_LIMIT_NEW_LISTING", "DERIVED"}
ELIGIBLE_EXECUTION = {"ELIGIBLE_SUBJECT_TO_LIQUIDITY", "CONDITIONAL_LIMIT_TOUCHED", "CONDITIONAL_NO_INTRADAY_RANGE"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _truth(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).upper() in {"TRUE", "1"}


def _single_pin(value):
    """Normalize FINAL GATE scalar pins without weakening exact matching."""
    if isinstance(value, list):
        if len(value) != 1:
            raise SnapshotMismatch("Taiwan FINAL GATE pin must contain exactly one value")
        return value[0]
    return value


def _directory_pin(value) -> str:
    """Accept the manifest's absolute Drive path while comparing its pinned leaf."""
    return Path(str(_single_pin(value))).name


def _manifest_hashes(directory: Path) -> dict[str, str]:
    path = directory / "manifest.json"
    if not path.is_file():
        raise SnapshotMismatch(f"Pinned manifest missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    hashes = manifest.get("sha256") or manifest.get("files_sha256") or manifest.get("outputs_sha256")
    if not isinstance(hashes, dict) or not hashes:
        raise SnapshotMismatch(f"Pinned manifest has no SHA map: {path}")
    for rel, expected in hashes.items():
        fp = directory / rel
        if not fp.is_file() or _sha256(fp) != str(expected):
            raise SnapshotMismatch(f"SHA mismatch: {fp}")
    return {str(k): str(v) for k, v in hashes.items()}


@dataclass(frozen=True)
class TaiwanPins:
    data_root: Path
    fubon_version_dir: Path
    quality_dir: Path
    corporate_actions_dir: Path
    trading_rules_dir: Path
    universe_dir: Path


class TaiwanEquityAdapter:
    """Read-only adapter for the section-26 SCOPED_GO Taiwan-equity snapshot."""

    def __init__(self, data_root: str | Path):
        root = Path(data_root).resolve()
        self.pins = TaiwanPins(root, root / "fubon_stock_5m/_manifest" / TW_FUBON_VERSION,
            *(root / p for p in TW_PINNED_PATHS.values()))
        self._verify_pins()

    def _verify_pins(self) -> None:
        p = self.pins
        final = p.data_root / "_tw-market-data-lake/audits/final_gate" / TW_FINAL_GATE_RUN / "run_manifest.json"
        if not final.is_file():
            raise SnapshotMismatch(f"Taiwan FINAL GATE manifest missing: {final}")
        run = json.loads(final.read_text(encoding="utf-8"))
        if run.get("run_id") != TW_FINAL_GATE_RUN or str(run.get("verdict", "")).replace(" ", "_") != TW_VERDICT:
            raise SnapshotMismatch("Taiwan FINAL GATE identity/verdict mismatch")
        pins = run.get("pins", {})
        expected = {
            ("fubon_5m", "seq"): TW_FUBON_SEQ,
            ("fubon_5m", "version_dir"): TW_FUBON_VERSION,
            ("quality_mask", "sha256"): TW_QUALITY_SHA256,
            ("quality_mask", "evidence_version"): TW_EVIDENCE_VERSION,
        }
        for (section, key), value in expected.items():
            if _single_pin(pins.get(section, {}).get(key)) != value:
                raise SnapshotMismatch(f"Taiwan pin mismatch: {section}.{key}")
        expected_dirs = {
            "corporate_actions_v2": TW_PINNED_PATHS["corporate_actions"].name,
            "trading_rules": TW_PINNED_PATHS["trading_rules"].name,
            "universe_pit": TW_PINNED_PATHS["universe"].name,
        }
        for section, expected_dir in expected_dirs.items():
            if _directory_pin(pins.get(section, {}).get("dir")) != expected_dir:
                raise SnapshotMismatch(f"Taiwan pin mismatch: {section}.dir")
        pointer = p.data_root / "fubon_stock_5m/_manifest/published.json"
        ptr = json.loads(pointer.read_text(encoding="utf-8")) if pointer.is_file() else {}
        if ptr.get("seq") != TW_FUBON_SEQ or ptr.get("version_dir") != TW_FUBON_VERSION:
            raise SnapshotMismatch("fubon_5m published pointer is not pinned seq 68")
        quality_file = p.quality_dir / "quality_mask_symbol_day.parquet"
        if not quality_file.is_file() or _sha256(quality_file) != TW_QUALITY_SHA256:
            raise SnapshotMismatch("Taiwan quality mask SHA mismatch")
        self._derived_hashes = {
            "corporate_actions": _manifest_hashes(p.corporate_actions_dir),
            "trading_rules": _manifest_hashes(p.trading_rules_dir),
            "universe": _manifest_hashes(p.universe_dir),
        }
        meta_path = p.fubon_version_dir / "meta.json"
        if not meta_path.is_file():
            raise SnapshotMismatch("fubon_5m pinned manifest meta is missing")
        self._fubon_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        parts_path = p.fubon_version_dir / "partitions.parquet"
        table_sha = self._fubon_meta.get("tables", {}).get("partitions", {}).get("sha256")
        if not parts_path.is_file() or not table_sha or _sha256(parts_path) != table_sha:
            raise SnapshotMismatch("fubon_5m partitions manifest mismatch")
        self._partitions = pd.read_parquet(parts_path)

    def _read_symbol_bars(self, symbol: str, start: str | None, end: str | None) -> tuple[pd.DataFrame, tuple[str, ...], tuple[str, ...]]:
        part_prefix = f"symbol={symbol}/"
        rows = self._partitions.loc[self._partitions["partition"].astype(str).str.startswith(part_prefix)]
        if rows.empty:
            raise PolicyViolation(f"No pinned fubon_5m partition for {symbol}")
        frames, files, ids = [], [], []
        for row in rows.itertuples(index=False):
            path = self.pins.data_root / "fubon_stock_5m" / str(row.relpath)
            if not path.is_file() or _sha256(path) != str(row.file_sha256):
                raise SnapshotMismatch(f"fubon_5m partition SHA mismatch: {path}")
            f = pd.read_parquet(path)
            if "bar_start" not in f.columns:
                raise PolicyViolation(f"Taiwan bar schema missing bar_start: {path}")
            frames.append(f)
            files.append(str(path))
            ids.append(str(row.partition))
        bars = pd.concat(frames, ignore_index=True)
        bars["symbol"] = symbol
        if "volume" not in bars.columns:
            if "volume_shares" in bars.columns:
                bars = bars.rename(columns={"volume_shares": "volume"})
                bars["volume_unit"] = bars.get("volume_unit", "share")
            elif "volume_raw" in bars.columns and "volume_unit" in bars.columns:
                # Keep the source quantity and its explicit unit together.  Do not
                # infer a lot-to-share conversion inside the access layer.
                bars = bars.rename(columns={"volume_raw": "volume"})
        required_ohlcv = {"open", "high", "low", "close", "volume"}
        if not required_ohlcv.issubset(bars.columns):
            raise PolicyViolation(f"Taiwan bar schema missing: {', '.join(sorted(required_ohlcv - set(bars.columns)))}")
        bars["timestamp"] = pd.to_datetime(bars["bar_start"], errors="raise")
        if bars["timestamp"].dt.tz is None:
            bars["timestamp"] = bars["timestamp"].dt.tz_localize("Asia/Taipei")
        else:
            bars["timestamp"] = bars["timestamp"].dt.tz_convert("Asia/Taipei")
        if start:
            s = pd.to_datetime(start)
            s = s.tz_localize("Asia/Taipei") if s.tzinfo is None else s.tz_convert("Asia/Taipei")
            bars = bars.loc[bars.timestamp >= s]
        if end:
            e = pd.to_datetime(end)
            e = e.tz_localize("Asia/Taipei") if e.tzinfo is None else e.tz_convert("Asia/Taipei")
            bars = bars.loc[bars.timestamp <= e]
        return bars, tuple(files), tuple(ids)

    def load(self, symbol: str, *, start: str | None = None, end: str | None = None,
             usage: str = "BACKTEST") -> UnifiedBars:
        symbol = str(symbol).strip()
        if not (len(symbol) == 4 and symbol.isdigit()):
            raise PolicyViolation("Taiwan Phase-2 scope is common stocks with 4-digit symbols only")
        bars, files, partitions = self._read_symbol_bars(symbol, start, end)
        bars["date"] = bars["timestamp"].dt.tz_localize(None).dt.normalize()

        quality = pd.read_parquet(self.pins.quality_dir / "quality_mask_symbol_day.parquet")
        quality["symbol"] = quality["symbol"].astype(str).str.zfill(4)
        quality["date"] = pd.to_datetime(quality["date"]).dt.normalize()
        q = quality.loc[quality.symbol == symbol]
        if q.empty or q["evidence_version"].nunique() != 1 or q["evidence_version"].iloc[0] != TW_EVIDENCE_VERSION:
            raise PolicyViolation(f"Missing or unpinned quality contract for {symbol}")

        universe = pd.read_parquet(self.pins.universe_dir / "universe_panel.parquet")
        universe["symbol"] = universe["symbol"].astype(str).str.zfill(4)
        universe["date"] = pd.to_datetime(universe["date"]).dt.normalize()
        u = universe.loc[universe.symbol == symbol]
        if u.empty:
            raise PolicyViolation(f"No PIT universe evidence for {symbol}")
        u = u.sort_values("date").copy()
        u["pit_eligible"] = u["official_listed"].shift(1).map(_truth).fillna(False)
        type_col = next((c for c in ("security_type", "instrument_class", "category") if c in u.columns), None)
        if type_col and u[type_col].astype(str).str.upper().isin(FUND_LIKE).any():
            raise PolicyViolation(f"{symbol} is outside the common-stock SCOPED_GO universe")

        rules = pd.read_parquet(self.pins.trading_rules_dir / "trading_rules.parquet")
        rules["symbol"] = rules["symbol"].astype(str).str.zfill(4)
        rules["date"] = pd.to_datetime(rules["date"]).dt.normalize()
        r = rules.loc[rules.symbol == symbol]
        if r.empty:
            raise PolicyViolation(f"No trading-rule evidence for {symbol}")

        out = bars.merge(q, on=["symbol", "date"], how="left", suffixes=("", "_quality"))
        out = out.merge(u, on=["symbol", "date"], how="left", suffixes=("", "_pit"))
        out = out.merge(r, on=["symbol", "date"], how="left", suffixes=("", "_rule"))
        required = ["can_use_for_price_backtest", "official_listed", "pit_eligible", "price_limit_status", "buy_eligibility", "sell_eligibility"]
        missing = [c for c in required if c not in out.columns]
        if missing:
            raise PolicyViolation(f"Taiwan contract columns missing: {', '.join(missing)}")
        out["data_exists"] = True
        out["research_eligible"] = out["official_listed"].map(_truth) & out["research_tier"].fillna("UNKNOWN").ne("ISOLATE")
        out["backtest_eligible"] = (out["research_eligible"] & out["pit_eligible"].map(_truth)
            & out["can_use_for_price_backtest"].map(_truth))
        base_eligible = (out["backtest_eligible"] & out["price_limit_status"].isin(USABLE_LIMIT_STATUS)
            & out["volume"].fillna(0).gt(0))
        out["buy_execution_eligible"] = (base_eligible & out["buy_eligibility"].isin(ELIGIBLE_EXECUTION))
        out["sell_execution_eligible"] = (base_eligible & out["sell_eligibility"].isin(ELIGIBLE_EXECUTION))
        clock = out["timestamp"].dt.strftime("%H:%M")
        out["session"] = clock.map(lambda x: "CLOSE_AUCTION" if "13:25" <= x <= "13:30" else "REGULAR")
        auction = out["session"].eq("CLOSE_AUCTION")
        out.loc[auction, ["buy_execution_eligible", "sell_execution_eligible"]] = False
        out["execution_eligible"] = out["buy_execution_eligible"] | out["sell_execution_eligible"]
        out["execution_rejection_reason"] = ""
        out["buy_execution_rejection_reason"] = ""
        out["sell_execution_rejection_reason"] = ""
        out.loc[~base_eligible, ["execution_rejection_reason", "buy_execution_rejection_reason", "sell_execution_rejection_reason"]] = "BAR_NOT_BACKTEST_OR_LIMIT_ELIGIBLE"
        out.loc[base_eligible & ~out["buy_eligibility"].isin(ELIGIBLE_EXECUTION), "buy_execution_rejection_reason"] = "BUY_RULE_INELIGIBLE"
        out.loc[base_eligible & ~out["sell_eligibility"].isin(ELIGIBLE_EXECUTION), "sell_execution_rejection_reason"] = "SELL_RULE_INELIGIBLE"
        out["execution_rejection_reason"] = out["buy_execution_rejection_reason"].where(
            out["buy_execution_rejection_reason"].ne(""), out["sell_execution_rejection_reason"])
        out.loc[auction, ["execution_rejection_reason", "buy_execution_rejection_reason", "sell_execution_rejection_reason"]] = "CLOSING_AUCTION_NOT_EXECUTABLE"
        out["bar_start"] = out["timestamp"]
        out["bar_end"] = out["bar_start"] + pd.Timedelta(minutes=5)
        out["available_at"] = out["bar_end"]
        out.loc[auction, "available_at"] = out.loc[auction, "bar_end"] + pd.Timedelta(minutes=3)
        out["session_open"] = out["bar_start"].dt.normalize() + pd.Timedelta(hours=9)
        out["session_close"] = out["bar_start"].dt.normalize() + pd.Timedelta(hours=13, minutes=30)
        out["timestamp_label"] = "START"
        out["market"], out["timezone"], out["timeframe"], out["contract"] = "TW_EQUITY", "Asia/Taipei", "5m", "CASH"
        if usage.upper() == "BACKTEST" and not out["backtest_eligible"].any():
            raise PolicyViolation(f"{symbol} has no bars inside the approved backtest scope")
        out = out.set_index("timestamp", drop=False).sort_index()
        if out.index.has_duplicates:
            raise PolicyViolation("Taiwan normalized data contains duplicate timestamps")
        provenance = UnifiedProvenance("TW", symbol, TW_FINAL_GATE_RUN, TW_FINAL_GATE_COMMIT, TW_VERDICT,
            files, partitions, ("S1 common-stock scope", "same-day mask post-hoc invalidation", "UNKNOWN fail-closed",
            "no closing-auction fill", "8101 quarantine"), TW_QUALITY_VERSION, "BACKTEST",
            assumptions=("cash dividends booked at effective session; actual payment date unavailable",))
        return UnifiedBars(out, provenance)
