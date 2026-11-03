from __future__ import annotations

from pathlib import Path

import pandas as pd

from .errors import PolicyViolation
from .ibkr import apply_exclusions, normalized_path, read_bars
from .models import LoadMetadata, LoadedBars, MarketDataConfig, UnifiedBars, UnifiedProvenance
from .policy import ALLOWED_VERDICTS, INSTRUMENTS
from .snapshot import verify_snapshot


class MarketDataClient:
    """Backward-compatible IBKR client plus the Phase-2 unified entrypoint."""

    def __init__(self, config: Path | MarketDataConfig):
        if isinstance(config, (str, Path)):
            config = MarketDataConfig(ibkr_run_dir=Path(config))
        self.config = config
        self.run_dir = Path(config.ibkr_run_dir).resolve() if config.ibkr_run_dir else None
        self.snapshot = verify_snapshot(self.run_dir) if self.run_dir else None
        self._txf = None
        self._tw = None

    @classmethod
    def from_run_dir(cls, run_dir: str | Path) -> "MarketDataClient":
        return cls(Path(run_dir))

    def _load_ibkr(self, instrument: str, *, start=None, end=None, contract=None, continuous=False) -> LoadedBars:
        if self.run_dir is None or self.snapshot is None:
            raise PolicyViolation("IBKR run directory is not configured")
        symbol = instrument.upper().strip()
        if symbol not in INSTRUMENTS:
            raise PolicyViolation(f"Unsupported instrument: {symbol}")
        policy = INSTRUMENTS[symbol]
        if policy.verdict not in ALLOWED_VERDICTS:
            raise PolicyViolation(f"Instrument verdict is not usable: {policy.verdict}")
        if continuous and policy.quarterly_contracts_only:
            raise PolicyViolation(f"{symbol} continuous-contract inference is forbidden")
        path = normalized_path(self.run_dir, policy, contract)
        frame = read_bars(path)
        before = len(frame)
        frame, excluded = apply_exclusions(frame, self.run_dir, policy, contract)
        if start is not None:
            frame = frame.loc[frame.index >= pd.to_datetime(start, utc=True)]
        if end is not None:
            frame = frame.loc[frame.index <= pd.to_datetime(end, utc=True)]
        return LoadedBars(frame, LoadMetadata(self.snapshot, symbol, policy.verdict, path, before, len(frame), excluded, start, end))

    def load(self, instrument: str | None = None, *, market: str | None = None, symbol: str | None = None,
             start: str | None = None, end: str | None = None, contract: str | None = None,
             continuous: bool = False, usage: str = "BACKTEST"):
        # Phase-1 compatibility: client.load("NQ", ...)
        if market is None:
            if instrument is None:
                raise TypeError("instrument is required")
            return self._load_ibkr(instrument, start=start, end=end, contract=contract, continuous=continuous)

        market_key = market.upper().strip()
        symbol_key = (symbol or instrument or "").upper().strip()
        if not symbol_key:
            raise PolicyViolation("symbol is required")
        if market_key == "IBKR":
            native = self._load_ibkr(symbol_key, start=start, end=end, contract=contract, continuous=continuous)
            frame = _standardize(native.frame, symbol_key, "IBKR", contract=contract, timezone="UTC")
            provenance = UnifiedProvenance("IBKR", symbol_key, native.metadata.snapshot.run_name,
                native.metadata.snapshot.producer_git_sha, native.metadata.verdict, (str(native.metadata.source_path),),
                exclusion_rules=((f"mandatory:{INSTRUMENTS[symbol_key].exclusions_filename}",) if INSTRUMENTS[symbol_key].exclusions_filename else ()),
                permitted_usage_scope="BACKTEST")
            return UnifiedBars(frame, provenance, native)
        if market_key == "TXF":
            if self.config.txf_run_dir is None:
                raise PolicyViolation("TXF run directory is not configured")
            from .txf import TXFAdapter, TXF_PRODUCER_SHA, TXF_RUN_NAME
            self._txf = self._txf or TXFAdapter.from_run_dir(self.config.txf_run_dir)
            native = self._txf.load(start=start, end=end)
            frame = _standardize(native.frame, "TXF", "TXF", contract_column="actual_contract_month", timezone="Asia/Taipei")
            return UnifiedBars(frame, UnifiedProvenance("TXF", "TXF", TXF_RUN_NAME, TXF_PRODUCER_SHA,
                native.metadata.verdict, tuple(str(p) for p in native.metadata.source_files),
                exclusion_rules=("bounded-formal-sample-only", "no-roll-inference"), permitted_usage_scope="BACKTEST",
                assumptions=(native.metadata.scope,)), native)
        if market_key in {"TW", "TAIWAN", "TAIWAN_EQUITY"}:
            if self.config.taiwan_lake_root is None:
                raise PolicyViolation("Taiwan data-lake root is not configured")
            from .taiwan import TaiwanEquityAdapter
            self._tw = self._tw or TaiwanEquityAdapter(self.config.taiwan_lake_root)
            return self._tw.load(symbol_key, start=start, end=end, usage=usage)
        raise PolicyViolation(f"Unsupported market: {market_key}")


def _standardize(frame: pd.DataFrame, symbol: str, market: str, *, contract: str | None = None,
                 contract_column: str | None = None, timezone: str) -> pd.DataFrame:
    out = frame.copy()
    ts = out.index if isinstance(out.index, pd.DatetimeIndex) else pd.to_datetime(out["timestamp"], utc=True)
    out["timestamp"] = pd.DatetimeIndex(ts)
    out["symbol"] = symbol
    out["market"] = ("IBKR_FUTURES" if market == "IBKR" and contract else
                     "IBKR_EQUITY" if market == "IBKR" else market)
    out["timezone"] = timezone
    out["timeframe"] = "1m"
    if contract_column and contract_column in out:
        out["contract"] = out[contract_column].astype(str)
    else:
        out["contract"] = contract
    # Keep the producer timestamp unchanged. Neither TXF nor the IBKR TSM
    # snapshot has confirmed bar-label/session semantics, so execution fields
    # are intentionally unavailable and both sides fail closed.
    if "session" not in out:
        out["session"] = "UNKNOWN"
    out["bar_start"] = pd.NaT
    out["bar_end"] = pd.NaT
    out["available_at"] = pd.NaT
    out["session_open"] = pd.NaT
    out["session_close"] = pd.NaT
    out["timestamp_label"] = "UNKNOWN"
    out["data_exists"] = True
    out["research_eligible"] = True
    out["backtest_eligible"] = True
    out["buy_execution_eligible"] = False
    out["sell_execution_eligible"] = False
    out["execution_eligible"] = False
    out["execution_rejection_reason"] = "TIMESTAMP_LABEL_OR_SESSION_UNKNOWN"
    out["buy_execution_rejection_reason"] = "TIMESTAMP_LABEL_OR_SESSION_UNKNOWN"
    out["sell_execution_rejection_reason"] = "TIMESTAMP_LABEL_OR_SESSION_UNKNOWN"
    return out
