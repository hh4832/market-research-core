from pathlib import Path

import pandas as pd

from .errors import PolicyViolation
from .ibkr import apply_exclusions, normalized_path, read_bars
from .models import LoadMetadata, LoadedBars
from .policy import ALLOWED_VERDICTS, INSTRUMENTS
from .snapshot import verify_snapshot


class MarketDataClient:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.resolve()
        self.snapshot = verify_snapshot(self.run_dir)

    @classmethod
    def from_run_dir(cls, run_dir: str | Path) -> "MarketDataClient":
        return cls(Path(run_dir))

    def load(
        self,
        instrument: str,
        *,
        start: str | None = None,
        end: str | None = None,
        contract: str | None = None,
        continuous: bool = False,
    ) -> LoadedBars:
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

        metadata = LoadMetadata(
            snapshot=self.snapshot,
            instrument=symbol,
            verdict=policy.verdict,
            source_path=path,
            rows_before_filter=before,
            rows_after_filter=len(frame),
            excluded_rows=excluded,
            requested_start=start,
            requested_end=end,
        )
        return LoadedBars(frame=frame, metadata=metadata)

