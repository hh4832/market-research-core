class DataAccessError(RuntimeError):
    """Base error for data-access failures."""


class SnapshotMismatch(DataAccessError):
    """The selected snapshot does not match a pinned release."""


class PolicyViolation(DataAccessError):
    """A request violates an approved data-use policy."""


class BacktestRejected(DataAccessError):
    """A backtest cannot continue without inventing data or execution."""
