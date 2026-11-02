# Data Access Contract v0.1

## Guarantees

1. The run directory name, producer Git SHA, overall verdict, and per-instrument
   verdicts are checked before access.
2. Only normalized Parquet from the pinned FULL run is readable.
3. `SCOPED_GO` exclusions are mandatory and fail closed when absent or ambiguous.
4. No forward fill, interpolation, zero-volume bar creation, or timestamp repair.
5. Every load returns snapshot, instrument, source path, verdict, and row counts.

## Explicit non-goals

- Raw acquisition, recovery, historical QC, or mutation of Google Drive data
- Continuous-futures construction or roll-rule inference
- Corporate-action inference
- Live trading or broker authentication

## TXF adapter (v0.2)

The formal run records a producer-computed canonical sample-matrix hash in its
run metadata. The adapter verifies that value separately from the raw-byte
SHA-256 of `diagnostics/predeclared_sample_matrix.csv`; the two hashes are not
interchangeable.

- Pins formal validation run `20261014_213932_404db609cf13`, producer commit
  `404db609cf136aae050e09f1e7712a7894f6ab6d`, and status `PASS WITH CAVEATS`.
- Verifies the run metadata, fixed sample-matrix SHA256, final integrity metrics
  and predeclared query plan before reading data.
- Reads only 58 `role=primary`, `contract_query=1!`, regular/afterhours files;
  requires `185` sampled trade dates and exactly `206,938` rows.
- Requires actual contract month and OHLCV on every row.
- Consolidates only byte-equivalent logical candles; conflicting duplicate
  timestamps fail closed.
- Uses `Asia/Taipei`, never fills missing minutes, never infers a roll rule, and
  does not change the existing Adaptive Close convention.

The formal validation run is the approved bounded snapshot. The separate 473
weekly research-cache partitions remain raw inputs and are not promoted by this
adapter.

## Adapter roadmap

- v0.1: IBKR FINAL GATE (`NQ`, `ES`, `TSM`, `SOXX`)
- v0.2: TXF adapter implemented; formal Drive smoke notebook ready
- v0.3: Taiwan equity adapter consuming seq 68, quality masks, PIT universe,
  corporate-action and tradability policies
- v0.4: stable backtest-facing protocol and experiment provenance
