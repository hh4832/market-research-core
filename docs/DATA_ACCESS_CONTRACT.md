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

- Reads only partitions explicitly listed in `txf_access_manifest.json`.
- Pins the recorded approved slice to `185` days / `206,938` rows and
  `SCOPED_GO`; this does not promote all 473 weekly cache files.
- Verifies each Parquet SHA256 against both the approval manifest and the
  immutable Fubon JSON sidecar.
- Requires actual contract month and OHLCV on every row.
- Consolidates only byte-equivalent logical candles; conflicting duplicate
  timestamps fail closed.
- Uses `Asia/Taipei`, never fills missing minutes, never infers a roll rule, and
  does not change the existing Adaptive Close convention.

The approval manifest is intentionally not fabricated by this repository. It
must be produced from the already-reviewed TXF validation slice after its exact
cache-to-validation mapping is recorded.

## Adapter roadmap

- v0.1: IBKR FINAL GATE (`NQ`, `ES`, `TSM`, `SOXX`)
- v0.2: TXF adapter implemented; formal Drive smoke waits for the approved
  cache-to-validation manifest
- v0.3: Taiwan equity adapter consuming seq 68, quality masks, PIT universe,
  corporate-action and tradability policies
- v0.4: stable backtest-facing protocol and experiment provenance
