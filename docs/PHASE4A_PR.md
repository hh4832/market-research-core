Bounded native TSM loading now requires explicit bindings to the existing manifest,
trusted SHA receipts, permitted dates and Holdout boundaries. It preserves native
timestamps/timezone and delegates complete15-source-bar conversion to the accepted
conservative policy. Candidate row groups crossing Holdout fail before scanning.

TAF/CAT use the existing per-unit regulatory fee field; SEC stays dated and SELL-only.
Separate source/rate/rounded-debit disclosures reconcile with the unchanged engine
and do not subtract slippage twice. Actual SEC/TAF numeric records are required inputs.

Dependency/base: `feature/m3a-ibkr-tsm-readiness` at Phase3 baseline
`699e3ad59951bbebb769bb69170be8d9a1a96c01` (PR #3). This is an independent Phase4
branch/PR, not a change pushed into Phase3. Retarget after the dependency merges.

Validation: Python3.11 and3.12 full suites each98 PASS. New cases use synthetic data
and deliberately synthetic fee records. No actual snapshot read, full-file hash,
real strategy run, FINAL GATE rerun, source modification or formal eligibility.

SCOPED_GO: actual manifest/schema/file SHA, trusted integrity receipt, Holdout
boundaries and existing SEC/TAF records were not available. Real bounded conversion
is PENDING. Contract: `docs/TSM_RESEARCH_ONLY_ADAPTER_CONTRACT.md`.
Machine evidence: `docs/diagnostics/tsm_research_only_adapter.json`.
