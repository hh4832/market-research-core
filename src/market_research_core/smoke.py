from dataclasses import asdict
from pathlib import Path

from .client import MarketDataClient
from .errors import PolicyViolation
from .policy import INSTRUMENTS


FUTURES_CONTRACTS = {
    "NQ": ("NQZ4", "NQH5", "NQM5", "NQU5", "NQZ5", "NQH6", "NQM6", "NQU6", "NQZ6"),
    "ES": ("ESZ4", "ESH5", "ESM5", "ESU5", "ESZ5", "ESH6", "ESM6", "ESU6", "ESZ6"),
}


def run_drive_smoke_test(run_dir: str | Path) -> dict:
    """Read the pinned snapshot without modifying it and verify published row counts."""
    client = MarketDataClient.from_run_dir(run_dir)
    instruments: dict[str, dict] = {}

    for symbol in ("TSM", "SOXX"):
        loaded = client.load(symbol)
        rows = len(loaded.frame)
        expected = INSTRUMENTS[symbol].expected_rows
        if rows != expected:
            raise PolicyViolation(f"{symbol} row count mismatch: expected {expected}, got {rows}")
        instruments[symbol] = {
            "verdict": loaded.metadata.verdict,
            "rows": rows,
            "excluded_rows_present_in_parquet": loaded.metadata.excluded_rows,
            "source": str(loaded.metadata.source_path),
        }

    for symbol, contracts in FUTURES_CONTRACTS.items():
        contract_rows: dict[str, int] = {}
        excluded_rows = 0
        for contract in contracts:
            loaded = client.load(symbol, contract=contract)
            contract_rows[contract] = len(loaded.frame)
            excluded_rows += loaded.metadata.excluded_rows
        rows = sum(contract_rows.values())
        expected = INSTRUMENTS[symbol].expected_rows
        if rows != expected:
            raise PolicyViolation(f"{symbol} aggregate row count mismatch: expected {expected}, got {rows}")
        instruments[symbol] = {
            "verdict": INSTRUMENTS[symbol].verdict,
            "rows": rows,
            "contracts": contract_rows,
            "excluded_rows_present_in_parquet": excluded_rows,
            "continuous_contract_created": False,
        }

    return {
        "status": "PASS",
        "read_only": True,
        "snapshot": asdict(client.snapshot),
        "instruments": instruments,
    }

