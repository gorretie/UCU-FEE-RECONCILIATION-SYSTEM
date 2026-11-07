"""Exceptions shared by the persistence and domain layers."""


class DataValidationError(ValueError):
    """Raised when an imported or manually entered record is invalid."""
