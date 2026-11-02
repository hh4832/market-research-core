class DataAccessError(RuntimeError):
    """Base error for data-access failures."""


class SnapshotMismatch(DataAccessError):
    """The selected snapshot does not match the pinned release."""


class PolicyViolation(DataAccessError):
    """A request violates an instrument or snapshot policy."""

