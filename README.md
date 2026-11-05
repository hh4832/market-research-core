# market-research-core

Read-only, fail-closed market-data access and reproducible research backtests.
The repository consumes approved snapshots; it never downloads broker data,
changes raw files, fills missing bars, infers futures rolls, or places orders.

## Approved scope

- IBKR FINAL GATE `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`
- TXF bounded formal run `20261014_213932_404db609cf13`
- Taiwan equity FINAL GATE `final_gate_20261031_154458_20eba73` (`SCOPED_GO`)
  with `fubon_5m` seq 68, quality 1.0.0, B3a 2.0.0, B4a 1.0.0 and B5a 1.0.0

Phase 2 adds a unified API without erasing native market rules.

```python
from market_research_core import MarketDataClient, MarketDataConfig

client = MarketDataClient(MarketDataConfig(
    ibkr_run_dir="/drive/.../20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL",
    txf_run_dir="/drive/.../20261014_213932_404db609cf13",
    taiwan_lake_root="/drive/MyDrive/00Quant_Research/data",
))

nq = client.load(market="IBKR", symbol="NQ", contract="NQH5")
txf = client.load(market="TXF", symbol="TXF")
tw = client.load(market="TW", symbol="2330", usage="BACKTEST")
```

The Phase-1 call remains supported:

```python
legacy = MarketDataClient.from_run_dir("/drive/.../IBKR_FINAL_GATE_FULL")
tsm = legacy.load("TSM")
```

Every unified result separates `data_exists`, `research_eligible`,
`backtest_eligible`, and `execution_eligible`, and includes pinned provenance.
Unavailable or `UNKNOWN` execution states fail closed.

## Deterministic integration backtest

```python
from market_research_core import BacktestAssumptions, BacktestEngine

result = BacktestEngine().run_sma(
    tw,
    BacktestAssumptions(fast_window=5, slow_window=20, initial_cash=1_000_000),
)
print(result.total_return, result.max_drawdown, result.trade_count)
```

The SMA crossover is an integration probe: a signal is formed only after its bar
is available and executes on a later eligible real bar. Output is labelled
`RESEARCH_INTEGRATION_DEMO`, not formal performance. Futures require an explicit
contract multiplier before any later phase may make a performance claim.

## Taiwan equity restrictions

The adapter consumes only the FINAL GATE section-26 scope. It rejects unpinned
manifests and SHA mismatches, non-four-digit symbols, fund-like instruments,
missing PIT membership, and missing contract fields. Same-day quality masks are
post-hoc execution invalidations, never universe-selection inputs. `UNKNOWN`
price-limit/tradability states and the 13:30 closing auction are not executable.
No last-price liquidation, corporate-action inference, or zero-volume bar is
created.

## Validation

```bash
pip install -e '.[test]'
pytest
```

Offline tests verify contracts but are not real-Drive evidence. Run the read-only
[`notebooks/colab_phase2_drive_smoke_test.ipynb`](notebooks/colab_phase2_drive_smoke_test.ipynb)
for formal three-adapter evidence. It consumes existing FINAL GATE artifacts and
does not rerun historical validation.

M3-A.4 engineering API, bounded synthetic evidence, cost JSON/YAML examples and
Agent field definitions: [Temporal & Cost Contract](docs/M3A4_TEMPORAL_COST_CONTRACT.md).
The label-agnostic research mode never upgrades real TSM to formal eligibility.
