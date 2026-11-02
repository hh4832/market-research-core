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

## Adapter roadmap

- v0.1: IBKR FINAL GATE (`NQ`, `ES`, `TSM`, `SOXX`)
- v0.2: TXF adapter consuming its approved manifest/session rules
- v0.3: Taiwan equity adapter consuming seq 68, quality masks, PIT universe,
  corporate-action and tradability policies
- v0.4: stable backtest-facing protocol and experiment provenance
