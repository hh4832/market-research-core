from .models import InstrumentPolicy, SnapshotIdentity

PINNED_SNAPSHOT = SnapshotIdentity(
    run_name="20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL",
    producer_git_sha="06359faf7ef4b8ff702a7ab1a6d07521d3b68f28",
    overall_verdict="SCOPED_GO",
)

INSTRUMENTS = {
    "NQ": InstrumentPolicy(
        "NQ", "SCOPED_GO", 869_291, "nq_normalized.parquet",
        "nq_unresolved_exclusions.csv", True,
    ),
    "ES": InstrumentPolicy(
        "ES", "SCOPED_GO", 869_313, "es_normalized.parquet",
        "es_unresolved_exclusions.csv", True,
    ),
    "TSM": InstrumentPolicy("TSM", "GO", 2_193_372, "tsm_normalized.parquet"),
    "SOXX": InstrumentPolicy(
        "SOXX", "SCOPED_GO", 2_065_882, "soxx_normalized.parquet",
        "soxx_rth_missing_intervals.csv",
    ),
}

ALLOWED_VERDICTS = {"GO", "SCOPED_GO"}

