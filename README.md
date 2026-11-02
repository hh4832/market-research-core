# market-research-core

Read-only, fail-closed data access for reproducible market research.

This repository is the boundary between validated market-data snapshots and
strategy/backtest code. It does not download broker data, modify raw files, infer
missing bars, or place orders.

## Current scope

- IBKR FINAL GATE snapshot `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`
- Producer commit `06359faf7ef4b8ff702a7ab1a6d07521d3b68f28`
- `TSM`: `GO`
- `NQ`, `ES`, `SOXX`: `SCOPED_GO`, with mandatory exclusions
- NQ/ES remain quarterly contracts; continuous-contract stitching is out of scope

## Quick start

```bash
pip install -e '.[test]'
market-data verify /path/to/20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL
```

```python
from market_research_core import MarketDataClient

client = MarketDataClient.from_run_dir(
    "/path/to/20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL"
)
bars = client.load("TSM", start="2020-01-01", end="2020-12-31")
# Futures always require an explicit quarterly contract:
nq = client.load("NQ", contract="NQH5")
print(bars.metadata)
```

`load()` only reads normalized Parquet from the pinned FULL run, verifies the
snapshot identity and instrument verdict, applies official exclusion files, and
returns provenance. It never forward-fills, interpolates, or synthesizes bars.

## Repository boundary

- `IBKR-market-data-cleaning-pipeline`: acquisition, recovery, QC, FINAL GATE
- `market-research-core`: read-only adapters, policy enforcement, backtests, agents

TXF is available through a separate fail-closed `TXFAdapter`. It consumes an
explicit approval manifest for the recorded 185-day / 206,938-row validation
slice; it does not treat all 473 weekly raw-cache partitions as validated.
Existing historical gates are consumed, not repeated.

## Colab Drive smoke test

Open [`notebooks/colab_drive_smoke_test.ipynb`](notebooks/colab_drive_smoke_test.ipynb)
in Colab and run all cells. It mounts Drive read-only from the application's
perspective, locates the pinned FULL run, checks TSM/SOXX, then reads all explicit
NQ/ES quarterly contracts and verifies aggregate published row counts. It never
constructs a continuous future.

TXF has a separate [Drive smoke notebook](notebooks/colab_txf_drive_smoke_test.ipynb).
It first confirms the raw-cache inventory, then requires the approved
cache-to-validation manifest. Until that manifest exists, the notebook stops
with `STOPPED_FAIL_CLOSED` and does not substitute all 473 partitions.

